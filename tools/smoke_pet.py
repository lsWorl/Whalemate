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
from aicompanion.ui.emilia_smoke_check import schedule_emilia_smoke_check

ROOT = Path(__file__).resolve().parents[1]


def use_placeholder_rig(mode: str, temp: Path) -> None:
    """自检不依赖本机 Coopanion：没有它（或指定 placeholder）时，生成并编译一套占位形象包，通过 AICOMPANION_RIG 交给桌宠。"""
    import os, sys
    if mode == 'coopanion': return
    if mode == 'auto':
        from aicompanion.body.rig_import import find_whale
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
    bad = {k: v for k, v in report.verified.items() if v}
    print(f'骨骼形象：十姿态分层包 {len(report.poses)} 个姿态，回拼比对' + ('全部零差异' if not bad else f'有差异 {bad}'), flush=True)
    os.environ['AICOMPANION_RIG'] = str(temp/'posepack-rig'/'rig.json')


def main():
    parser = argparse.ArgumentParser(description='Offline desktop pet verification.')
    parser.add_argument('--preview', action='store_true', help='Show a temporary pet for up to 90 seconds.')
    parser.add_argument('--emilia', action='store_true', help='Verify the original illustration mesh effect, not Cubism.')
    parser.add_argument('--rig', choices=('auto', 'placeholder', 'coopanion'), default='auto',
                        help='骨骼形象来源：auto = 本机有 Coopanion 就用大肥鱼，没有就用合成的占位形象包')
    parser.add_argument('--posepack', metavar='DIR', help='用十姿态分层包（pose-layered-v2 目录）自检，优先于 --rig')
    args = parser.parse_args()
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as temp:
        if args.posepack: use_posepack(args.posepack, Path(temp))
        else: use_placeholder_rig(args.rig, Path(temp))
        database = Database(Path(temp)/'pet.sqlite3'); database.initialize()
        settings = Settings('AICompanion', database.database_path, 'mock', 'https://example.invalid', '', 'mock', .8, 10)
        registry = CharacterRegistry(database, settings); registry.sync()
        if args.emilia:
            database.set_setting('avatar_mode:emilia','animated-illustration'); registry.set_active('emilia')
        controller = ChatController(database, MemoryService(database), MockLLMClient())
        controller.character = registry.active()
        window = MainWindow(controller, settings, registry)
        window.pet.show()
        if args.preview:
            # 预览：角色常驻 90 秒，可以亲手戳、拖、双击输入、右键菜单。
            QTimer.singleShot(1200, window.pet.drop_in); QTimer.singleShot(90000, window.request_exit)
            # 预览里自动说一句话，展示用户气泡、思考图标、回复气泡和台词里的表情动作
            QTimer.singleShot(30000, lambda: window.submit('你好呀，今天过得怎么样？'))
            state = None
        elif args.emilia: state=schedule_emilia_smoke_check(window,database,ROOT/'docs/emilia-smoke')
        else: state = schedule_pet_smoke_check(window, database, ROOT/'docs/pet-v2/smoke')
        code = app.exec()
        return code if state is None else 0 if state.get('passed') and code==0 else 1


if __name__ == '__main__': raise SystemExit(main())
