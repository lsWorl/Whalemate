"""使用临时数据和 mock 进行真实 Qt 渲染与生命周期验证，不读取私人聊天。"""
from pathlib import Path
import tempfile
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from aicompanion.storage.database import Database
from aicompanion.services.memory_service import MemoryService
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.config import Settings
from aicompanion.ui.main_window import MainWindow


app=QApplication([]); app.setQuitOnLastWindowClosed(False)
temp=tempfile.TemporaryDirectory(); db=Database(Path(temp.name)/'smoke.sqlite3'); db.initialize()
settings=Settings('AICompanion',db.database_path,'mock','https://example.invalid','', 'mock',.8,10)
controller=ChatController(db,MemoryService(db),MockLLMClient()); window=MainWindow(controller,settings)
window.show()
if '--speech' in sys.argv:
    window.speak_toggle.setChecked(True)
    window.speech.speaking.connect(lambda active:print('SPEAKING',active,flush=True))
if hasattr(window.avatar,'bridge'):
    window.avatar.bridge.loaded.connect(lambda status:print('AVATAR',status,flush=True))
    window.avatar.loadFinished.connect(lambda ok:print('PAGE',ok,flush=True))
else:
    print('AVATAR native-sprites',type(window.avatar).__name__,flush=True)
QTimer.singleShot(1500,lambda:window.submit('今天有点累，想和你说说话。'))
QTimer.singleShot(1800,lambda:window.submit('先听我说就好。'))
if '--settings' in sys.argv:
    from aicompanion.ui.settings_window import SettingsWindow
    QTimer.singleShot(5000,lambda:SettingsWindow(controller,settings,window).show())
def inspect():
    target=Path(__file__).resolve().parents[1]/'docs/ui-smoke.png'
    window.grab().save(str(target))
    print('SCREENSHOT',target,flush=True)
    print('MESSAGES',[(r['role'],r['status']) for r in db.recent_messages()],flush=True)
    window.request_exit()
QTimer.singleShot(8000,inspect)
QTimer.singleShot(20000,window.request_exit)
code=app.exec(); print('EXIT',code,flush=True); temp.cleanup()
