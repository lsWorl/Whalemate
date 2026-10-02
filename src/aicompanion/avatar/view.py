from pathlib import Path
import json
from PySide6.QtCore import QObject, Signal, Slot, QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings


ASSETS = Path(__file__).resolve().parents[1]/'assets/live2d'


class AvatarBridge(QObject):
    stateChanged = Signal(str)
    modelChanged = Signal(str)
    loaded = Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.path = ASSETS/'Haru/Haru.model3.json'

    @Slot(result=str)
    def initialModel(self):
        return QUrl.fromLocalFile(str(self.path)).toString()

    @Slot(str)
    def report(self,value):
        self.loaded.emit(value)


class AvatarView(QWebEngineView):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.bridge = AvatarBridge(self)
        self.channel = QWebChannel(self.page())
        self.channel.registerObject('avatar',self.bridge)
        self.page().setWebChannel(self.channel)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,True)
        self.setMinimumWidth(260)
        self.load(QUrl.fromLocalFile(str(ASSETS/'index.html')))

    def set_state(self,mood='calm',mouth=0):
        self.bridge.stateChanged.emit(json.dumps({'mood':mood,'mouth':max(0,min(1,mouth))}))

    def set_model(self,path):
        candidate = Path(path).resolve() if path else ASSETS/'Haru/Haru.model3.json'
        if candidate.is_file():
            self.bridge.path = candidate
            self.bridge.modelChanged.emit(QUrl.fromLocalFile(str(candidate)).toString())
