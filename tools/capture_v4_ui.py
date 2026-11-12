"""用临时数据库与本地模拟回复截取界面，分别用 --scale 1 和 --scale 1.5 运行。"""
import argparse
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QFontDatabase, QHoverEvent, QPainter
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QRadioButton, QScrollBar, QSpinBox, QStyle, QStyleOptionButton, QStyleOptionSlider, QTabBar, QTextEdit, QVBoxLayout, QWidget)

from aicompanion.characters.registry import CharacterRegistry
from aicompanion.config import Settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.providers.router import ModelRouter
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow
from aicompanion.ui.onboarding import Onboarding, VoiceCard
from aicompanion.ui.settings_window import SettingsWindow


def capture_controls(app, save_image):
    """用真实 Qt 控件展示通用主题的状态，随产品截图一起核对 DPI。"""
    from PySide6.QtTest import QTest
    from aicompanion.ui import theme
    from aicompanion.ui.icons import icon_button
    from aicompanion.ui.settings_layout import Section

    gallery = QDialog()
    gallery.setStyleSheet(theme.DIALOG)
    grid = QGridLayout(gallery)
    grid.setContentsMargins(24, 20, 24, 20)
    grid.setSpacing(12)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    heading = QLabel('控件规范')
    heading.setObjectName('PageTitle')
    grid.addWidget(heading, 0, 0, 1, 2)
    hovered = []

    def button(text, variant='', state=''):
        sample = QPushButton(text)
        sample.setObjectName(variant)
        sample.setEnabled(state != 'disabled')
        if state == 'hover':
            hovered.append(sample)
        return sample

    def row(*widgets):
        line = QHBoxLayout()
        for widget in widgets:
            line.addWidget(widget)
        line.addStretch()
        return line

    buttons = Section('按钮')
    form = QFormLayout()
    for title, variant in (('主按钮', 'Primary'), ('次按钮', '')):
        form.addRow(title, row(button('下一步' if variant else '测试一下', variant), button('悬停', variant, 'hover'),
                               button('不可用', variant, 'disabled')))
    form.addRow('文字', row(button('上一步', 'Flat'), button('先跳过', 'Link'), button('删除', 'Danger'),
                            button('悬停', 'Danger', 'hover')))
    speak = icon_button('voice', '朗读')
    speak.setCheckable(True)
    speak.setChecked(True)
    form.addRow('图标', row(icon_button('gear', '设置'), speak, icon_button('send', '发送', primary=True)))
    buttons.add(form)
    buttons.box.addStretch()
    grid.addWidget(buttons, 1, 0)

    fields = Section('输入、下拉和数字框')
    form = QFormLayout()
    focused = QLineEdit('鲸鲸')
    invalid = QLineEdit('12a45')
    theme.set_state(invalid, 'invalid', True)
    error = QLabel('QQ 号应该是 5 到 12 位数字。')
    error.setObjectName('FieldError')
    disabled = QLineEdit('由角色包决定')
    disabled.setEnabled(False)
    combo = QComboBox()
    combo.addItems(['妹妹（叫你哥哥）', '女儿（叫你爹）', '朋友'])
    combo.setCurrentIndex(1)
    number = QSpinBox()
    number.setRange(1, 999)
    number.setValue(130)
    number.setSuffix(' 分钟')
    for title, field in (('普通', QLineEdit('鲸鲸')), ('焦点', focused), ('有问题', invalid), ('', error),
                         ('不可用', disabled), ('下拉', combo), ('数字', number)):
        form.addRow(title, field)
    fields.add(form)
    grid.addWidget(fields, 1, 1)

    checks = Section('复选、单选和开关')
    for kind, title in ((QCheckBox, '复选'), (QRadioButton, '单选')):
        group = []
        for text in ('已选', '未选', '悬停', '不可用'):
            item = kind(text)
            item.setChecked(text == '已选')
            item.setEnabled(text != '不可用')
            if text == '悬停':
                hovered.append(item)
            group.append(item)
        checks.add(row(QLabel(title), *group))
    cards = QHBoxLayout()
    for title, description, selected in (('电脑自带的声音', '已选', True), ('鲸鲸专属声线', '未选', False)):
        card = VoiceCard(title, description)
        card.choice.setChecked(selected)
        cards.addWidget(card)
    checks.add(cards)
    switches = []
    for value, name in ((True, '开'), (False, '关')):
        switch = QCheckBox(name)
        switch.setObjectName('Switch')
        switch.setChecked(value)
        switches.append(switch)
    checks.add(row(QLabel('总开关'), *switches))
    segment = QFrame()
    segment.setObjectName('Segments')
    modes = QHBoxLayout(segment)
    modes.setContentsMargins(3, 3, 3, 3)
    for name in ('聊天', '帮我做事'):
        mode = button(name, 'Mode')
        mode.setCheckable(True)
        mode.setChecked(name == '聊天')
        modes.addWidget(mode)
    checks.add(row(segment))
    tabs = QTabBar()
    tabs.addTab('QQ 聊天')
    tabs.addTab('社区与黑话')
    checks.add(row(tabs))
    checks.box.addStretch()
    grid.addWidget(checks, 2, 0)

    details = Section('列表和多行输入')
    options = QLabel()
    options.setFixedHeight(126)
    options.setAlignment(Qt.AlignmentFlag.AlignCenter)
    details.add(options)
    plain = QPlainTextEdit('普通多行输入')
    rich = QTextEdit('富文本输入')
    for field in (plain, rich):
        field.setFixedHeight(56)
    editors = QHBoxLayout()
    editors.addWidget(plain)
    editors.addWidget(rich)
    details.add(editors)
    grid.addWidget(details, 2, 1)

    statuses = Section('清单状态')
    marks = QHBoxLayout()
    for style, glyph, title in (('StepReady', '✓', '已完成'), ('StepRequired', '!', '必需、未完成'), ('StepPending', '4', '可选')):
        mark = QLabel(glyph)
        mark.setFixedSize(28, 28)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setObjectName(style)
        marks.addWidget(mark)
        marks.addWidget(QLabel(title))
    marks.addStretch()
    statuses.add(marks)
    grid.addWidget(statuses, 3, 0)
    bars = Section('滚动条')
    for name in ('普通', '悬停'):
        bar = QScrollBar(Qt.Orientation.Horizontal)
        bar.setValue(35)
        bar.setPageStep(55)
        sample = QHBoxLayout()
        sample.addWidget(QLabel(name))
        sample.addWidget(bar)
        bars.add(sample)
        if name == '悬停':
            hovered.append(bar)
    grid.addWidget(bars, 3, 1)
    theme.prepare_controls(gallery)
    gallery.resize(1060, 840)
    gallery.show()
    gallery.activateWindow()
    app.processEvents()
    # 弹窗由 Qt 单独绘制。取正在展开的真实列表，包括选中行、悬停行和阴影。
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    QTest.mouseMove(view.viewport(), view.visualRect(combo.model().index(2, 0)).center())
    app.processEvents()
    options.setPixmap(view.window().grab())
    combo.hidePopup()
    focused.setFocus()
    app.processEvents()

    # 一次只会有一个真实鼠标指针。用控件自己的样式选项绘制其悬停状态，
    # 让两档 DPI 的状态图都与产品 QSS 同步，不依赖离屏插件的鼠标命中位置。
    QTest.mouseMove(gallery, QPoint(2, 2))
    app.processEvents()
    sheet = gallery.grab()
    painter = QPainter(sheet)
    for widget in hovered:
        painter.save()
        painter.translate(widget.mapTo(gallery, QPoint()))
        painter.setClipRect(widget.rect())
        if isinstance(widget, QScrollBar):
            # 150% 时图库底部可能超出虚拟屏幕。在屏幕内以同尺寸的真实滑块取样。
            host = QWidget()
            host.setStyleSheet(theme.COMMON)
            sample = QScrollBar(widget.orientation(), host)
            sample.setRange(widget.minimum(), widget.maximum())
            sample.setPageStep(widget.pageStep())
            sample.setValue(widget.value())
            sample.resize(widget.size())
            host.resize(sample.size())
            host.move(40, 40)
            host.show()
            app.processEvents()
            option = QStyleOptionSlider()
            sample.initStyleOption(option)
            handle = sample.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                                   QStyle.SubControl.SC_ScrollBarSlider, sample)
            pos = handle.center()
            QTest.mouseMove(sample, pos)
            sample.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
            QApplication.sendEvent(sample, QHoverEvent(QEvent.Type.HoverMove, QPointF(pos),
                                                       QPointF(sample.mapToGlobal(pos)), QPointF(pos)))
            app.processEvents()
            painter.drawPixmap(handle.topLeft(), host.grab(handle))
            host.close()
        else:
            option = QStyleOptionButton()
            widget.initStyleOption(option)
            option.state |= QStyle.StateFlag.State_MouseOver
            element = QStyle.ControlElement.CE_PushButton
            if isinstance(widget, QCheckBox):
                element = QStyle.ControlElement.CE_CheckBox
            elif isinstance(widget, QRadioButton):
                element = QStyle.ControlElement.CE_RadioButton
            widget.style().drawControl(element, option, painter, widget)
        painter.restore()
    painter.end()
    save_image(sheet, 'controls.png')
    gallery.close()


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

    def save_image(image, filename):
        if abs(image.devicePixelRatio() - scale) > .01 or not image.save(str(out / filename)):
            raise RuntimeError('截图或缩放验证失败：' + filename)

    def shot(widget, filename):
        widget.show()
        app.processEvents()
        save_image(widget.grab(), filename)

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
            if wizard.pages.currentWidget().verticalScrollBar().maximum():
                raise RuntimeError(f'默认窗口下向导第 {i + 1} 步出现滚动条')
        wizard.reject()
        dialog = SettingsWindow(controller, settings, window, speech_output=window.speech)
        dialog.panel_opacity.setValue(18)
        dialog.save()
        for key in dialog.PAGES:
            dialog.open_page(key)
            shot(dialog, f'settings-{key}.png')
            if key == 'overview' and dialog.nav.pages[key].verticalScrollBar().maximum():
                raise RuntimeError('默认尺寸下概览出现滚动条')
        dialog.reject()
        capture_controls(app, save_image)
        window.pet.shutdown()
        window.agent.shutdown()
        window.tray.hide()
        print(f'{out} · 20 张 · {scale:g}x')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale', type=float, choices=(1, 1.5), required=True)
    capture(parser.parse_args().scale)
