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


def main():
    parser = argparse.ArgumentParser(description='Offline desktop pet verification.')
    parser.add_argument('--preview', action='store_true', help='Show a temporary pet for up to 90 seconds.')
    parser.add_argument('--emilia', action='store_true', help='Verify the original illustration mesh effect, not Cubism.')
    args = parser.parse_args()
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    app = QApplication([]); app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory() as temp:
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
