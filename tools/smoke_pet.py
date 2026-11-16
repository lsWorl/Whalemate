"""临时桌宠窗口验收或预览；不读取 .env、不联网、不打开麦克风。"""
import argparse
from dataclasses import replace
import json
import os
import shutil
from pathlib import Path
import tempfile
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from aicompanion.config import Settings
from aicompanion.characters.registry import CharacterRegistry
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow
from aicompanion.ui.pet_smoke_check import schedule_pet_smoke_check

ROOT = Path(__file__).resolve().parents[1]


def install_preview_animation(source, target):
    from aicompanion.characters.animation_pack import load_animation_pack
    from aicompanion.pet.state import PRIORITY

    shutil.copytree(source, target)
    manifest = target/'animations.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    actions = data['actions']
    poses = data.setdefault('poses', {})
    idle = poses.get('idle', 'idle')
    if idle not in actions:
        raise ValueError('预览素材包缺少待机动作。')
    # 仅启用选中的素材；缺失动作共用待机图集，无需重复解码或复制图片。
    data.pop('replacementPack', None)
    for name in set(poses) | set(actions) | set(PRIORITY) | {'walking'}:
        action = poses.get(name, name)
        poses[name] = action if action in actions else idle
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    load_animation_pack(manifest)
    print(f'预览动画包 {source.resolve()}；可用动作 {", ".join(actions)}，其余动作回退到 {idle}。', flush=True)


def configure_test_pack(registry, args, temp):
    """默认直接使用产品角色包；显式选项只替换临时注册表里的测试角色。"""
    whale = registry.pack('deepseek-whale')
    if args.animation_pack:
        from aicompanion.characters import load_pack
        from aicompanion.ui import aboutme_preview, icons

        clean = temp/whale.id
        # 预览素材放到角色清单实际读取的动画目录，桌宠显示的就是指定素材。
        folder = Path(whale.renderer['animation']).parent.name
        shutil.copytree(whale.root, clean, ignore=shutil.ignore_patterns('animation-aboutme', folder))
        install_preview_animation(args.animation_pack, clean/folder)
        registry.packs[whale.id] = load_pack(clean)
        # 设置页和首次启动向导也可能打开动画预览，让两处都指向同一份临时素材。
        aboutme_preview.WHALE = icons.WHALE = clean
        return
    if args.without_aboutme:
        from aicompanion.characters import load_pack
        # 本项验证随程序提供的自绘形象，忽略可能指向其他本机模型的开发者覆盖值。
        os.environ.pop('AICOMPANION_RIG', None)
        clean = temp/whale.id
        clean.mkdir()
        for name in ('character.json', 'lines.json', 'interjections.json', 'qq-chat-card.json'):
            shutil.copy2(whale.root/name, clean/name)
        for name in ('rig', 'sprites'):
            shutil.copytree(whale.root/name, clean/name)
        registry.packs[whale.id] = load_pack(clean)
        return
    if args.posepack:
        from aicompanion.body.pose_pack import compile_posepack
        compile_posepack(Path(args.posepack), temp/'posepack-rig', verify=True)
        rig_path = temp/'posepack-rig'/'rig.json'
    elif args.rig == 'placeholder':
        from make_placeholder_pack import make
        from aicompanion.body.rig_pack import compile_pack
        compile_pack(make(temp/'placeholder-art', 1024), temp/'placeholder-rig')
        rig_path = temp/'placeholder-rig'/'rig.json'
    elif args.rig == 'coopanion':
        from aicompanion.body.rig_import import ensure_whale_rig
        rig_path = ensure_whale_rig(ROOT, root=temp)
        if rig_path is None:
            raise FileNotFoundError('找不到指定的本机 Coopanion 形象')
    else:
        return
    # 专项骨骼测试仍走产品的 rig 渲染路径，编译产物只留在临时目录。
    os.environ['AICOMPANION_RIG'] = str(rig_path)
    renderer = dict(whale.renderer, kind='sprites', path='sprites/sprites.json', rig={'source': 'pack'})
    renderer.pop('animation', None)
    registry.packs[whale.id] = replace(whale, renderer=renderer)


def main():
    parser = argparse.ArgumentParser(description='Offline desktop pet verification.')
    parser.add_argument('--preview', action='store_true', help='显示可交互的临时桌宠。')
    parser.add_argument('--preview-seconds', type=int, metavar='N', help='预览持续秒数，默认 90 秒')
    parser.add_argument('--animation-pack', type=Path, metavar='DIR', help='仅预览该目录的动画素材包')
    parser.add_argument('--rig', choices=('auto', 'placeholder', 'coopanion'), default='auto',
                        help='auto 按本机安装情况显示 Aboutme 或随附的自绘鲸娘；其余选项用于骨骼专项自检')
    parser.add_argument('--posepack', metavar='DIR', help='用十姿态分层包（pose-layered-v2 目录）自检，优先于 --rig')
    parser.add_argument('--without-aboutme', action='store_true', help='用包含自绘骨骼和立绘的临时角色包验证未安装 Aboutme 的状态')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'docs/pet-v2/smoke', help='自检截图和报告目录')
    parser.add_argument('--local-embedding-dir', type=Path, help='内存测量用的已校验模型缓存，只从磁盘读取')
    parser.add_argument('--measure-ready', type=Path, help='向内存测量脚本报告预览已就绪')
    args = parser.parse_args()
    if (args.animation_pack or args.preview_seconds is not None) and not args.preview:
        parser.error('--animation-pack 和 --preview-seconds 仅用于 --preview')
    if args.animation_pack and (args.without_aboutme or args.posepack or args.rig != 'auto'):
        parser.error('--animation-pack 与其他形象来源选项不能同时使用')
    if args.preview_seconds is not None and not 1 <= args.preview_seconds <= 2_147_483:
        parser.error('--preview-seconds 超出计时器支持的范围')
    if (args.local_embedding_dir or args.measure_ready) and not args.preview:
        parser.error('内存测量参数需要 --preview')
    if args.without_aboutme and (args.posepack or args.rig != 'auto'):
        parser.error('--without-aboutme 与骨骼专项选项不能同时使用')
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as temp:
        database = Database(Path(temp)/'pet.sqlite3'); database.initialize()
        if args.animation_pack:
            database.set_setting('pet_hide_fullscreen', False)
            database.set_setting('life_enabled', False)
        settings = Settings('AICompanion', database.database_path, offline=True)
        registry = CharacterRegistry(database, settings)
        configure_test_pack(registry, args, Path(temp))
        registry.sync()
        controller = ChatController(database, MemoryService(database), MockLLMClient())
        controller.character = registry.active()
        window = MainWindow(controller, settings, registry)
        # 临时验收/预览不能清理用户真正的回收区和 DSH 会话。
        from aicompanion.agent.housekeeping import Report
        window.agent.housekeep = Report
        window.pet.show()
        if args.preview:
            QTimer.singleShot((args.preview_seconds or 90) * 1000, window.request_exit)
            if not args.animation_pack:
                QTimer.singleShot(1200, window.pet.drop_in)
            # 预览里自动说一句话，展示用户气泡、思考图标、回复气泡和台词里的表情动作
            if args.measure_ready:
                service = controller.semantic
                if args.local_embedding_dir:
                    database.upsert_memory('合成测量：水族馆里的鲸鱼', controller.character.id, 'perf:whale')
                    service.local_root = args.local_embedding_dir
                    service.enable_local(download=False)
                    app.aboutToQuit.connect(service.close)
                def ready():
                    if args.local_embedding_dir and service.state != '向量索引就绪':
                        QTimer.singleShot(150, ready)
                        return
                    args.measure_ready.write_text('ready', encoding='ascii')
                ready()
            elif not args.animation_pack:
                QTimer.singleShot(30000, lambda: window.submit('你好呀，今天过得怎么样？'))
            state = None
        else: state = schedule_pet_smoke_check(window, database, args.output_dir)
        code = app.exec()
        return code if state is None else 0 if state.get('passed') and code==0 else 1


if __name__ == '__main__': raise SystemExit(main())
