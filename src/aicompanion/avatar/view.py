from pathlib import Path
import json
from PySide6.QtCore import QObject, Signal, Slot, QUrl, Qt
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtGui import QColor
from aicompanion.avatar.model_package import validate_model_package, ModelPackageError


ASSETS = Path(__file__).resolve().parents[1]/'assets/live2d'


class AvatarBridge(QObject):
    stateChanged = Signal(str)
    modelChanged = Signal(str)
    loaded = Signal(str)
    surfaceChanged = Signal(bool)
    pointerChanged = Signal(str)
    menuRequested = Signal()
    motionChanged = Signal(str)

    def __init__(self,parent=None):
        super().__init__(parent)
        # 不再自带示例模型；未指定时显示艾米莉娅 2D 网格动画。
        self.path = ASSETS/'Emilia/emilia.animation.json'
        self.desktop = False
        self.state = {'mood': 'calm', 'mouth': 0}

    @Slot(result=str)
    def initialModel(self):
        return QUrl.fromLocalFile(str(self.path)).toString()

    @Slot(str)
    def report(self,value):
        self.loaded.emit(value)

    @Slot(result=bool)
    def initialSurface(self): return self.desktop

    @Slot(result=str)
    def initialState(self): return json.dumps(self.state)

    @Slot(str)
    def pointer(self, kind):
        if self.desktop and kind in ('press', 'move', 'release'): self.pointerChanged.emit(kind)

    @Slot()
    def menu(self):
        if self.desktop: self.menuRequested.emit()


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
        self.bridge.state = {'mood':mood,'mouth':max(0,min(1,mouth))}
        self.bridge.stateChanged.emit(json.dumps(self.bridge.state))

    def set_surface(self, desktop):
        self.bridge.desktop = bool(desktop)
        self.setMinimumWidth(0 if desktop else 260)
        # 冷启动时 WebEngine 还没有原生表面，必须在第一次显示前声明透明，
        # 仅设置页面背景颜色会在 Windows 合成时产生不透明的黑色矩形。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,desktop)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,False)
        self.setAutoFillBackground(False)
        self.page().setBackgroundColor(QColor(0, 0, 0, 0) if desktop else QColor('#edf6f3'))
        self.setStyleSheet('background:transparent;' if desktop else '')
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bridge.surfaceChanged.emit(self.bridge.desktop)

    def set_model(self,path):
        if not path:
            self.bridge.loaded.emit('error:尚未导入 Live2D 模型'); return False
        try:
            candidate = validate_model_package(path)
        except (ModelPackageError, OSError) as error:
            self.bridge.loaded.emit('error:'+str(error)); return False
        self._load_path(candidate)
        return True

    def _load_path(self, candidate):
        self.bridge.path = candidate
        self.bridge.modelChanged.emit(QUrl.fromLocalFile(str(candidate)).toString())

    def set_preview(self):
        # 使用独立 PNG 预览入口，不伪造 model3.json 或宣称图片已经完成 Cubism 绑定。
        self._load_path(ASSETS/'Emilia/emilia-fanart.png')

    def set_animated_emilia(self):
        # 独立的动画立绘格式，不进入 MOC3 校验或冒称 Cubism 模型。
        self._load_path(ASSETS/'Emilia/emilia.animation.json')

    def play_motion(self, name):
        if name in ('greet','nod','shake','tilt','shy','wave'):
            self.bridge.motionChanged.emit(name)
