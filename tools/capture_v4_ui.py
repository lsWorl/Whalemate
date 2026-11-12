"""用临时数据库与本地模拟回复截取界面，分别用 --scale 1 和 --scale 1.5 运行。"""
import argparse
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from aicompanion.characters.registry import CharacterRegistry
from aicompanion.config import Settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.providers.router import ModelRouter
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow
from aicompanion.ui.onboarding import Onboarding
from aicompanion.ui.settings_window import SettingsWindow


def capture(scale):
    os.environ['QT_SCALE_FACTOR'] = str(scale)
    # 固定物理屏幕大小。Qt 默认离屏屏幕仅 800×800，在 150% 下会把菜单挤成两列。
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as config_dir:
        config = Path(config_dir) / 'screen.json'
        config.write_text(json.dumps({'screens': [{'name': '验收', 'width': 1920, 'height': 1080,
                                                   'logicalDpi': 96, 'logicalBaseDpi': 96}]}), encoding='utf-8')
        os.environ['QT_QPA_PLATFORM'] = 'offscreen:configfile=' + config.relative_to(Path.cwd()).as_posix()
        app = QApplication([])
    # Windows 的 offscreen 插件没有系统字体库；显式载入设计指定的本机字体。
    for file in ('msyh.ttc', 'msyhbd.ttc', 'seguisym.ttf'):
        if QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / file)) < 0:
            raise RuntimeError('离屏截图缺少微软雅黑：' + file)
    app.setFont(QFont('Microsoft YaHei UI'))
    app.setQuitOnLastWindowClosed(False)
    out = Path(__file__).resolve().parents[1] / 'output' / 'v4-ui' / f'{int(100 * scale)}pct'
    out.mkdir(parents=True, exist_ok=True)

    def shot(widget, filename):
        widget.show()
        app.processEvents()
        image = widget.grab()
        if abs(image.devicePixelRatio() - scale) > .01 or not image.save(str(out / filename)):
            raise RuntimeError('截图或缩放验证失败：' + filename)

    with tempfile.TemporaryDirectory() as folder:
        temp = Path(folder)
        os.environ['LOCALAPPDATA'] = str(temp)
        db = Database(temp / 'demo.sqlite3')
        db.initialize()
        settings = Settings('鲸伴', db.database_path, offline=True, storage_paths_file=temp / 'locations.json')
        registry = CharacterRegistry(db, settings)
        registry.sync()
        controller = ChatController(db, MemoryService(db), MockLLMClient())
        controller.character = registry.active()
        window = MainWindow(controller, settings, registry)
        window.pump.stop()
        window.scheduler.stop()
        window.agent.housekeep = lambda: None
        window._append_message('你', '今天有点累，想和你说说话。')
        window._append_message(controller.character.name, '我在呢，慢慢说。是工作上的事，还是心里有点闷？')
        window._append_message('你', '帮我把桌面上的截图整理到一个文件夹吧')
        window.agent_toggle.setChecked(True)
        window.task_card.detail.setText('已找到 23 张截图，准备移到“桌面/截图整理”。')
        window.task_card.actions.show()
        window.task_card.show()
        shot(window, 'chat.png')
        menu = window.pet.build_menu()
        menu.resize(menu.sizeHint())
        shot(menu, 'pet-menu.png')
        menu.close()
        controller.router = ModelRouter(db, settings)
        wizard = Onboarding(window)
        for i in range(5):
            wizard.show_step(i)
            shot(wizard, f'wizard-{i + 1}.png')
        wizard.reject()
        dialog = SettingsWindow(controller, settings, window)
        dialog.panel_opacity.setValue(18)
        dialog.save()
        for key in dialog.PAGES:
            dialog.open_page(key)
            shot(dialog, f'settings-{key}.png')
        dialog.reject()
        window.pet.shutdown()
        window.agent.shutdown()
        window.tray.hide()
        print(f'{out} · 19 张 · {scale:g}x')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale', type=float, choices=(1, 1.5), required=True)
    capture(parser.parse_args().scale)
