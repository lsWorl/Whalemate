"""V4-UI 的首次启动、凭据入口、条件菜单和防抖边界，只使用临时数据库。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
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

    def test_header_and_conditional_actions(self):
        win = MainWindow(self.controller, self.settings, self.registry)
        try:
            header = win.findChild(QWidget, 'Header')
            self.assertNotIn(self.router.label(self.controller.character.id),
                             ' '.join(x.text() for x in header.findChildren(QLabel)))
            self.assertIn('还没连上模型', win.presence.text())
            self.assertIsNone(win.name_label.menu())
            pet = win.pet
            menu = pet.build_menu()
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
        import threading
        win = MainWindow(self.controller, self.settings, self.registry)
        try:
            box = {'event': threading.Event(), 'answer': 'timeout'}
            spec = {'text': '允许移动测试文件吗？', 'details': '源文件 → 目标目录',
                    'choices': [('同意', 'once'), ('让她停下', 'no')]}
            win.agent._ask(spec, box)
            bubble = win.pet.confirm
            self.assertEqual(win.task_card.details.toPlainText(), spec['details'])
            win.task_card.respond('once')
            self.assertTrue(box['event'].is_set())
            self.assertEqual(box['answer'], 'once')
            self.assertTrue(bubble.answered)
            self.assertIsNone(win.task_card.answer)
            second = {'event': threading.Event(), 'answer': 'timeout'}
            win.agent._ask(spec, second)
            win.pet.close_confirm()
            win.task_card.respond('once')
            self.assertEqual(second['answer'], 'timeout')
        finally:
            win.pet.shutdown()
            win.tray.hide()
            win.agent.shutdown()
            win.deleteLater()


    def test_install_progress_and_cancel_without_network(self):
        import json
        wizard = Onboarding(self.host)
        wizard.show_step(3)
        folder = Path(self.temp.name)
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
