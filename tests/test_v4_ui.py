"""V4-UI 的首次启动、凭据入口、条件菜单和防抖边界，只使用临时数据库。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from aicompanion.characters.registry import CharacterRegistry
from aicompanion.config import Settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.router import ModelRouter
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow
from aicompanion.ui.onboarding import Onboarding, needs_onboarding
from aicompanion.ui.settings_window import SettingsWindow


class FakeProtector:
    name = 'v4-ui-test'

    def protect(self, data):
        return data[::-1]

    def unprotect(self, data):
        return data[::-1]


class RedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # 离屏验收只用 Qt 控件，避免无音频后端机器加载麦克风、播放器或 TTS。
        self.enterContext(patch('PySide6.QtMultimedia.QMediaDevices.audioInputs', return_value=[]))
        speech = self.enterContext(patch('aicompanion.ui.main_window.SpeechOutput')).return_value
        speech.busy = False
        speech.workers = ()
        # QQ 后台轮询由 test_bridge_page 覆盖，不能让它在临时库释放后再回调。
        self.enterContext(patch('aicompanion.ui.bridge_page.BridgePage.refresh', lambda page: None))
        from types import SimpleNamespace
        from aicompanion.ui.sprite_view import PlaceholderAvatar
        self.enterContext(patch.object(MainWindow, '_apply_avatar',
            lambda window: window.pet.set_view(PlaceholderAvatar('测试'), 'placeholder')))
        def fake_tray(window):
            window.tray = SimpleNamespace(hide=lambda: None, isVisible=lambda: False)
            window.pet_action = QAction(window)
        self.enterContext(patch.object(MainWindow, '_build_tray', fake_tray))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete)
        root = Path(self.temp.name)
        self.db = Database(root / 'test.sqlite3')
        self.db.initialize()
        self.settings = Settings('鲸伴', self.db.database_path, offline=True, storage_paths_file=root / 'paths.json')
        self.registry = CharacterRegistry(self.db, self.settings)
        self.registry.sync()
        self.router = ModelRouter(self.db, self.settings, CredentialStore(self.db, {}, protector=FakeProtector()))
        self.controller = ChatController(self.db, MemoryService(self.db), self.router)
        self.controller.character = self.registry.active()
        self.host = QWidget()
        self.host.controller = self.controller
        self.addCleanup(self.host.deleteLater)

    def test_skip_welcome_and_close_offline(self):
        self.assertTrue(needs_onboarding(self.controller))
        wizard = Onboarding(self.host)
        wizard.skip.click()
        self.assertEqual(wizard.pages.currentIndex(), 4)
        wizard.back.click()
        self.assertEqual(wizard.pages.currentIndex(), 3)
        wizard.reject()
        self.assertFalse(needs_onboarding(self.controller))
        self.assertFalse(self.router.configured('chat', self.controller.character.id))

    def test_offline_step_and_credentials_use_settings_account(self):
        wizard = Onboarding(self.host)
        wizard.next.click()
        wizard.skip.click()
        self.assertEqual(wizard.pages.currentIndex(), 2)
        self.assertFalse(wizard.admin.accounts())
        wizard.back.click()
        self.assertEqual(wizard.key.echoMode(), QLineEdit.EchoMode.Password)
        wizard.key.setText('v4-fake-input')
        with patch.object(wizard.admin, 'add_account', wraps=wizard.admin.add_account) as add:
            wizard.reject()
            add.assert_called_once()
        account, = wizard.admin.accounts()
        self.assertEqual(self.router.credentials.get(account.credential, endpoint=account.base_url), 'v4-fake-input')
        self.assertNotIn('v4-fake-input', str(self.db.get_setting('model_config')))
        self.assertTrue(self.router.configured('chat', self.controller.character.id))
        self.db.set_setting('onboarding_done', False)
        config = self.router.store.load()
        config.bindings.clear()
        self.router.store.save(config)
        self.assertFalse(needs_onboarding(self.controller))
        self.assertTrue(self.db.get_setting('onboarding_done'))

    def test_autosave_debounces_validates_and_flushes(self):
        dialog = SettingsWindow(self.controller, self.settings)
        self.addCleanup(dialog.deleteLater)
        events = []
        dialog.saved.connect(events.append)
        dialog.name.setText('鲸')
        QTest.qWait(100)
        dialog.name.setText('小鲸')
        self.assertNotEqual(self.db.characters()[0].name, '小鲸')
        QTest.qWait(650)
        self.assertEqual(self.db.characters()[0].name, '小鲸')
        self.assertEqual(events.count('identity'), 1)
        self.assertRegex(dialog.save_status.text(), r'✓ 已自动保存 · \d\d:\d\d')
        dialog.bridge_page.owner.setText('12x')
        QTest.qWait(650)
        self.assertIn('QQ', dialog.save_status.text())
        self.assertFalse(dialog.bridge_page.owner_error.isHidden())
        self.assertFalse(self.db.get_setting('bridge_owner:qq', ''))
        dialog.bridge_page.owner.setText('123456')
        dialog.model.setText('missing.model3.json')
        dialog.avatar_mode.setCurrentIndex(dialog.avatar_mode.findData('live2d'))
        dialog.accept()
        self.assertEqual(self.db.get_setting('bridge_owner:qq'), '123456')
        self.assertNotEqual(self.controller.character.model_path, 'missing.model3.json')
        self.assertFalse(dialog.model_error.isHidden())
        dialog.reject()

    def test_voice_card_and_checkbox_row_are_clickable(self):
        wizard = Onboarding(self.host)
        wizard.show_step(2)
        wizard.show()
        self.app.processEvents()
        card = wizard.voice_cards[1]
        hint = card.findChild(QLabel)
        QTest.mouseClick(hint, Qt.MouseButton.LeftButton)
        self.assertTrue(card.property('selected'))
        self.assertEqual(wizard.voices.checkedId(), 1)
        self.assertTrue(card.isAncestorOf(wizard.listen))
        self.assertEqual(wizard.pages.currentWidget().verticalScrollBar().maximum(), 0)
        smart = wizard.smart
        before = smart.isChecked()
        QTest.mouseClick(smart, Qt.MouseButton.LeftButton, pos=QPoint(smart.width()-2, smart.height()//2))
        self.assertNotEqual(smart.isChecked(), before)
        wizard.reject()

    def test_storage_drafts_do_not_touch_disk_until_confirmed(self):
        import os
        from PySide6.QtWidgets import QMessageBox
        from aicompanion.storage.locations import read_record
        self.enterContext(patch.dict(os.environ, {'SystemDrive': 'Z:'}))
        dialog = SettingsWindow(self.controller, self.settings)
        self.addCleanup(dialog.deleteLater)
        page = dialog.storage_page
        root = Path(self.temp.name)
        record = self.settings.storage_paths_file
        directories = []
        for suffix in ('part', 'final'):
            memory, memes = root / ('memory-' + suffix), root / ('memes-' + suffix)
            directories.extend((memory, memes))
            page.memory.setText(str(memory))
            page.memes.setText(str(memes))
            QTest.qWait(650)
            self.assertTrue(dialog.save())
            self.assertFalse(any(path.exists() for path in directories))
            self.assertFalse(record.exists())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No) as confirm:
            page.apply_button.click()
            confirm.assert_called_once()
            self.assertEqual(confirm.call_args.args[2].splitlines()[:2],
                             [f'记忆和聊天目录：{memory}', f'表情包目录：{memes}'])
        self.assertFalse(record.exists())
        self.assertFalse(any(path.exists() for path in directories))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes) as confirm:
            page.apply_button.click()
            page.apply_button.click()
            confirm.assert_called_once()
        self.assertEqual(Path(read_record(record)['migration']['target']).parent, memory)
        self.assertTrue(any(memes.iterdir()))
        self.assertFalse(any(path.exists() for path in directories[:2]))
        self.assertEqual(dialog.bridge_page.meme_root.text(), str(memes))
        page.memory.setText(str(root / 'discarded'))
        dialog.reject()
        self.assertFalse((root / 'discarded').exists())
        self.assertEqual(Path(read_record(record)['migration']['target']).parent, memory)

    def test_connection_drafts_are_only_applied_together(self):
        from aicompanion.bridge.server import runtime
        from aicompanion.bridge.service import setting
        dialog = SettingsWindow(self.controller, self.settings)
        self.addCleanup(dialog.deleteLater)
        page = dialog.bridge_page
        page.advanced.setChecked(True)
        original_port = setting(self.db, 'bridge_port')
        original_token = setting(self.db, 'bridge_token')
        self.db.set_setting('qq_mode', 'existing')
        sync = self.enterContext(patch('aicompanion.qq.configure.plugin_config'))
        with patch.object(runtime, 'apply', return_value='') as apply:
            page.enabled.setChecked(True)
            page.port.setValue(8089)
            page.token.setText('draft')
            page.owner.setText('123456')
            QTest.qWait(650)
            self.assertEqual(self.db.get_setting('bridge_owner:qq'), '123456')
            self.assertEqual(setting(self.db, 'bridge_port'), original_port)
            self.assertEqual(setting(self.db, 'bridge_token'), original_token)
            self.assertFalse(setting(self.db, 'bridge_enabled'))
            page.enabled.setChecked(False)
            page.port.setValue(8090)
            page.token.setText('final-test-token')
            page.enabled.setChecked(True)
            QTest.qWait(650)
            self.assertTrue(dialog.save())
            page._sync_from_db(preserve_drafts=True)
            self.assertTrue(page.apply_button.isEnabled())
            apply.assert_not_called()
            sync.assert_not_called()
            page.apply_button.click()
            apply.assert_called_once_with(self.db)
            self.assertEqual(setting(self.db, 'bridge_port'), 8090)
            self.assertEqual(setting(self.db, 'bridge_token'), 'final-test-token')
            page.apply_button.click()
            apply.assert_called_once()
            sync.assert_called_once_with('', 'http://127.0.0.1:8090', 'final-test-token')
            page.port.setValue(9123)
            dialog.reject()
            self.assertEqual(setting(self.db, 'bridge_port'), 8090)

    def test_overview_counts_required_steps_with_aligned_statuses(self):
        dialog = SettingsWindow(self.controller, self.settings)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.overview_title.text(), '还差 1 步就全部准备好')
        self.assertEqual((dialog.readiness.value(), dialog.readiness.maximum()), (2, 3))
        self.db.set_setting('speech_enabled', False)
        with patch.object(self.router, 'configured', return_value=True):
            dialog._refresh_overview()
        self.assertEqual(dialog.overview_title.text(), '可以开始了')
        self.assertEqual(dialog.readiness.value(), 3)
        self.assertEqual([mark.width() for mark, _, _ in dialog.checklist], [28] * 5)
        self.assertEqual(sum(w.text() == '可选' for w in dialog.findChildren(QLabel)), 2)
        dialog.reject()

    def test_header_and_conditional_actions(self):
        win = MainWindow(self.controller, self.settings, self.registry)
        win.scheduler.stop()
        win.pump.stop()
        try:
            header = win.findChild(QWidget, 'Header')
            self.assertNotIn(self.router.label(self.controller.character.id),
                             ' '.join(x.text() for x in header.findChildren(QLabel)))
            self.assertIn('还没连上模型', win.presence.text())
            self.assertIsNone(win.name_label.menu())
            pet = win.pet
            menu = pet.build_menu()
            header_action = menu.actions()[0]
            self.assertFalse(header_action.isEnabled())
            self.assertTrue(header_action.defaultWidget().isEnabled())
            actions = [a.text() for a in menu.actions()]
            self.assertEqual([a.text() for a in menu.actions() if a.text() and not a.isEnabled()],
                             ['和她说话', '让她帮忙', '她的样子'])
            self.assertNotIn('换个角色', actions)
            self.assertNotIn('打断回复', actions)
            pet.reply_active = pet.agent_busy = pet.call_active = True
            pet.characters.append(('demo', '测试角色'))
            actions = [a.text() for a in pet.build_menu().actions()]
            for action in ('换个角色', '打断回复', '让她停下手里的活', '结束语音聊天'):
                self.assertIn(action, actions)
        finally:
            win.pet.shutdown()
            win.pet.deleteLater()
            win.tray.hide()
            win.agent.shutdown()
            win.deleteLater()


    def test_wrong_service_clears_key_and_test_uses_worker(self):
        import threading
        from dataclasses import replace
        from aicompanion.providers.admin import ModelAdmin
        self.router.settings = replace(self.settings, offline=False)
        wizard = Onboarding(self.host)
        self.host.tasks = set()
        self.host._task_finished = self.host.tasks.discard
        wizard.show_step(1)
        wizard.key.setText('first-demo-key')
        wizard.services.button(1).click()
        self.assertFalse(wizard.key.text())
        wizard.key.setText('second-demo-key')
        threads = []
        def fake_reply(admin, cid, token):
            threads.append(threading.get_ident())
            return '演示模型：我醒啦！'
        with patch.object(ModelAdmin, 'test_chat', fake_reply):
            wizard.test.click()
            worker = wizard.worker
            self.assertTrue(worker.wait(3000))
            self.app.processEvents()
        self.assertIn('我醒啦', wizard.model_status.text())
        self.assertNotEqual(threads, [threading.get_ident()])
        wizard.services.button(0).click()
        wizard.test.click()
        self.assertIsNone(wizard.worker)
        self.assertIn('请先填写密钥', wizard.model_status.text())
        self.assertEqual(len(wizard.admin.accounts()), 1)
        wizard.next.click()
        wizard.back.click()
        wizard.reject()
        self.assertEqual(len(wizard.admin.accounts()), 1)

    def test_task_card_and_pet_share_one_approval(self):
        from aicompanion.agent.approval import Rule
        win = MainWindow(self.controller, self.settings, self.registry)
        win.scheduler.stop()
        win.pump.stop()
        queue = win.agent.tasks.approvals
        def ask(rule=None):
            req = queue.add(0, rule.category if rule else 'move', '允许移动测试文件吗？', '源文件 → 目标目录', rule)
            win.agent._refresh_approvals()
            return req
        try:
            for action, button in [('once', win.task_card.allow), ('always', win.task_card.reuse),
                                   ('persist', win.task_card.persist), ('no', win.task_card.deny)]:
                req = ask(Rule('move', str(Path(self.temp.name))))
                bubble = win.pet.confirm
                self.assertEqual(win.task_card.details.toPlainText(), '源文件 → 目标目录')
                with patch('PySide6.QtWidgets.QInputDialog.getText', return_value=('先等等', True)):
                    button.click()
                self.assertTrue(req.ready.is_set())
                self.assertEqual(req.decision, action)
                if action == 'no':
                    self.assertEqual(req.reason, '先等等')
                self.assertTrue(bubble.answered)
                self.assertIsNone(win.task_card.answer)
            second = ask()
            win.pet.close_confirm()
            win.task_card.respond('once')
            self.assertEqual(second.decision, 'timeout')
            first = ask(Rule('move', str(Path(self.temp.name))))
            hard = ask()
            last = ask(Rule('command', 'set-content'))
            self.assertIn('3', win.task_card.queue_count.text())
            from PySide6.QtWidgets import QMessageBox
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes) as confirm:
                win.task_card.batch.click()
            self.assertIn(first.code, confirm.call_args.args[2])
            self.assertIn(last.code, confirm.call_args.args[2])
            self.assertNotIn(hard.code, confirm.call_args.args[2])
            self.assertEqual((first.decision, last.decision, hard.decision), ('once', 'once', ''))
        finally:
            win.pet.shutdown()
            win.pet.deleteLater()
            win.tray.hide()
            win.agent.shutdown()
            win.deleteLater()


    def test_install_progress_and_cancel_without_network(self):
        import json
        folder = Path(self.temp.name)
        # 安装流程必须从“尚未安装”的合成目录开始，与真机已有的 Aboutme 资源隔离。
        with patch('aicompanion.ui.icons.WHALE', folder):
            wizard = Onboarding(self.host)
        wizard.show_step(3)
        wizard.script = folder / 'demo_installer.py'
        wizard.script.write_text("import time\nprint('已安装 idle', flush=True)\ntime.sleep(20)\n", encoding='utf-8')
        (folder / 'aboutme-pet.lock.json').write_text(json.dumps({'actions': [{}, {}]}), encoding='utf-8')
        wizard.install_or_cancel()
        self.assertTrue(wizard.process.waitForStarted(3000))
        for _ in range(60):
            if wizard.progress.value() == 1:
                break
            QTest.qWait(50)
        self.assertEqual(wizard.progress.value(), 1)
        self.assertEqual(wizard.progress.maximum(), 2)
        wizard.install_or_cancel()
        wizard.process.waitForFinished(3000)
        self.app.processEvents()
        self.assertIn('已取消', wizard.install_status.text())
        self.assertTrue(wizard.install.isEnabled())
        wizard.reject()
