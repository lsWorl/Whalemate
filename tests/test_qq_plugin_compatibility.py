"""旧版桥接插件仍能接话。主动发件箱按每个请求的版本决定，设置卡显示运行中的版本和升级办法。"""
import json
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from aicompanion.bridge.outbox import OutboxStore
from aicompanion.bridge.server import BridgeServer, runtime
from test_bridge_page import QApplication
from test_qq_proactive import Base


class CompatibilityTests(Base):
    def setUp(self):
        super().setUp()
        self.server = BridgeServer(self.service, 0, 'synthetic')
        self.addCleanup(self.server.close)
        self.db.set_setting('community_web', False)
        self.db.set_setting('community_learning', False)

    def post(self, path, payload):
        request = Request(f'http://127.0.0.1:{self.server.port}/v1/{path}',
                          headers={'Authorization': 'Bearer synthetic', 'Content-Type': 'application/json'},
                          data=json.dumps(payload).encode())
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def beat(self, version):
        return self.post('heartbeat', {'astrbot': version, 'onebot': {'connected': True, 'self_id': '90009'}})

    def test_downgrade_keeps_item_for_capable_client_and_accepts_ordinary_chats(self):
        self.beat('plugin-0.10.2')
        turn = self.service.proactive_qq('合成的主动问候')
        self.assertTrue(turn)
        for old in ('plugin-0.10.0', 'plugin-0.10.1', ''):
            with self.subTest(version=old):
                self.assertNotIn('outbox', self.beat(old))
                self.assertFalse(self.service.qq_ready())
                self.assertEqual(OutboxStore(self.db).get(turn)['status'], 'queued')
        self.model.reply = '收到啦'
        self.beat('plugin-0.10.0')
        for group in ('', '30003'):
            response = self.post('chat', {'platform': 'qq', 'sender_id': '10001', 'group_id': group,
                                          'text': '收到没', 'called': True})
            self.assertEqual(response['reply'], '收到啦')
        item, = self.beat('plugin-0.10.2')['outbox']
        self.assertEqual(item['id'], turn)
        self.assertTrue(item['require_plugin_version'])
        for old_claim in ({'id': turn}, {'id': turn, 'astrbot': 'plugin-0.10.1'}):
            self.assertFalse(self.post('outbox/claim', old_claim)['allowed'])
        self.assertTrue(self.post('outbox/claim', {'id': turn, 'astrbot': 'plugin-0.10.2'})['allowed'])

    @unittest.skipIf(QApplication is None, '需要 PySide6')
    def test_settings_card_shows_running_version_and_upgrades_both_install_modes(self):
        from aicompanion.ui.bridge_page import BridgePage
        self.app = QApplication.instance() or QApplication([])
        self.db.set_setting('qq_mode', 'existing')
        self.db.set_setting('qq_root', str(Path(self.temp.name)/'qq'))
        existing = Path(self.temp.name)/'existing'
        (existing/'data'/'plugins').mkdir(parents=True)
        self.db.set_setting('qq_existing_root', str(existing))
        self.beat('plugin-0.10.0')
        with mock.patch.object(runtime, 'server', self.server), mock.patch.object(BridgePage, 'refresh', lambda page: None):
            page = BridgePage(self.db)
            page.timer.stop()
            self.addCleanup(page.deleteLater)
            for mode in ('existing', 'auto'):
                with self.subTest(mode=mode), mock.patch.object(page.qq, 'ready', return_value=[]), \
                        mock.patch.object(page.qq, 'supervisor') as supervisor, mock.patch.object(page.qq, 'webui') as webui:
                    self.db.set_setting('qq_mode', mode)
                    supervisor.exited_with.return_value = None
                    webui.return_value.login_status.return_value = {'isLogin': True}
                    webui.return_value.login_info.return_value = {'uin': '90009'}
                    page._show_status(page.qq.diagnose())
                    self.assertIn('主动', page.title.text())
                    for word in ('0.10.0', '0.10.2', '重载' if mode == 'existing' else '重启'):
                        self.assertIn(word, page.detail.text())
                        self.assertIn(word, page.qq.diagnostics_text())
                    self.assertFalse(page.action.isHidden())
                    self.assertIn('更新', page.action.text())
                    with mock.patch.object(page, '_run', side_effect=lambda fn, text: fn()), \
                            mock.patch.object(page.qq, 'stop') as stop, mock.patch.object(page.qq, 'start') as start:
                        page.action.click()
                    if mode == 'existing':
                        metadata = existing/'data/plugins/astrbot_plugin_aicompanion/metadata.yaml'
                        self.assertIn('v0.10.2', metadata.read_text(encoding='utf-8'))
                    else:
                        stop.assert_called_once()
                        start.assert_called_once()
                    self.assertEqual(page.qq.diagnose().code, 'plugin_outdated')
            self.db.set_setting('qq_mode', 'existing')
            self.beat('plugin-0.10.2')
            page._show_status(page.qq.diagnose())
            self.assertEqual(page.status.code, 'connected')
            self.assertTrue(self.service.qq_ready())
