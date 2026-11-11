"""临时桌宠窗口验收或预览；不读取 .env、不联网、不打开麦克风。"""
import argparse
from pathlib import Path
import tempfile
from PySide6.QtCore import QTimer, Qt
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


def use_placeholder_rig(mode: str, temp: Path) -> None:
    """自检不依赖本机 Coopanion：没有它（或指定 placeholder）时，生成并编译一套占位形象包，通过 AICOMPANION_RIG 交给桌宠。"""
    import os, sys
    if mode == 'coopanion': return
    if mode == 'auto':
        from aicompanion.body.rig_import import find_whale
        own = ROOT/'src'/'aicompanion'/'characters'/'deepseek-whale'/'rig'/'rig.json'
        if own.is_file():
            print('骨骼形象：仓库自带的十姿态鲸娘', flush=True); return
        if find_whale(ROOT): return
    try:
        sys.path.insert(0, str(ROOT/'tools'))
        from make_placeholder_pack import make
        from aicompanion.body.rig_pack import compile_pack
        compile_pack(make(temp/'placeholder-art', 1024), temp/'placeholder-rig')
        os.environ['AICOMPANION_RIG'] = str(temp/'placeholder-rig'/'rig.json')
        print('骨骼形象：合成占位形象包', flush=True)
    except ImportError as error:   # 没装 Pillow：退回整张立绘
        print('没有生成占位形象包（需要 Pillow）：', error, flush=True)


def use_posepack(source: str, temp: Path) -> None:
    """自检十姿态分层包：把 pose-layered-v2 编译到临时目录（带回拼比对），通过 AICOMPANION_RIG 交给桌宠。"""
    import os
    from aicompanion.body.pose_pack import compile_posepack
    report = compile_posepack(Path(source), temp/'posepack-rig', verify=True)
    print(f'骨骼形象：十姿态分层包 {len(report.poses)} 个姿态，回拼比对 {len(report.verified)} 个状态全部零差异', flush=True)
    os.environ['AICOMPANION_RIG'] = str(temp/'posepack-rig'/'rig.json')


def main():
    parser = argparse.ArgumentParser(description='Offline desktop pet verification.')
    parser.add_argument('--preview', action='store_true', help='Show a temporary pet for up to 90 seconds.')
    parser.add_argument('--preview-seconds', type=int, default=90)
    parser.add_argument('--motion-preview', action='store_true', help='性能用真实拖动和走跑回放')
    parser.add_argument('--motion-start', type=int, default=15)
    parser.add_argument('--performance-log', type=Path)
    parser.add_argument('--rig', choices=('auto', 'placeholder', 'coopanion'), default='auto',
                        help='骨骼形象来源：auto = 仓库自带的十姿态鲸娘 → 本机 Coopanion 大肥鱼 → 合成的占位形象包')
    parser.add_argument('--posepack', metavar='DIR', help='用十姿态分层包（pose-layered-v2 目录）自检，优先于 --rig')
    args = parser.parse_args()
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as temp:
        if args.posepack: use_posepack(args.posepack, Path(temp))
        else: use_placeholder_rig(args.rig, Path(temp))
        database = Database(Path(temp)/'pet.sqlite3'); database.initialize()
        settings = Settings('AICompanion', database.database_path, offline=True)
        registry = CharacterRegistry(database, settings); registry.sync()
        controller = ChatController(database, MemoryService(database), MockLLMClient())
        controller.character = registry.active()
        window = MainWindow(controller, settings, registry)
        # 临时验收/预览不能清理用户真正的回收区和 DSH 会话。
        from aicompanion.agent.housekeeping import Report
        window.agent.housekeep = Report
        window.pet.show()
        if args.preview:
            if args.performance_log:
                import json,time
                database.set_setting('pet_hide_fullscreen',False);window.pet.input_locked=True
                presence=[];presence_started=time.monotonic();monitor=QTimer(window);monitor.setInterval(1000)
                monitor.timeout.connect(lambda:presence.append({'seconds':round(time.monotonic()-presence_started,3),'visible':window.pet.isVisible()}));monitor.start()
                app.aboutToQuit.connect(lambda:args.performance_log.write_text(json.dumps(presence,indent=2),encoding='utf-8'))
            # 预览：默认90秒；测量使用独立时长和内存设置，不影响用户数据库。
            QTimer.singleShot(1200, window.pet.drop_in); QTimer.singleShot(args.preview_seconds*1000, window.request_exit)
            if args.motion_preview:
                from motion_performance import MotionPerformance
                window.speech.enabled=False;window.speak_toggle.setChecked(False);window.hide()
                motion=MotionPerformance(window.pet,ROOT/'output/v2-check/perf-motion-behavior.json',window)
                QTimer.singleShot(args.motion_start*1000,motion.start);app.aboutToQuit.connect(motion.save)
            # 预览里自动说一句话，展示用户气泡、思考图标、回复气泡和台词里的表情动作
            if not args.motion_preview: QTimer.singleShot(30000, lambda: window.submit('你好呀，今天过得怎么样？'))
            state = None
        else: state = schedule_pet_smoke_check(window, database, ROOT/'docs/pet-v2/smoke')
        code = app.exec()
        return code if state is None else 0 if state.get('passed') and code==0 else 1


if __name__ == '__main__': raise SystemExit(main())
