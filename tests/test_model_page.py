"""模型页（计划书 v4 功能一）：账户与 Key、按能力选择模型、角色单独设置、测试连接。不联网。"""
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:   # pragma: no cover
    QApplication = None

from aicompanion.providers.admin import ModelAdmin, agent_route
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.router import ModelRouter
from aicompanion.storage.database import Database


class FakeProtector:
    name = 'fake'
    def protect(self, data): return data[::-1]
    def unprotect(self, data): return data[::-1]


def make_router(root):
    db = Database(Path(root) / 'm.sqlite3'); db.initialize()
    env = {}
    creds = CredentialStore(db, env, protector=FakeProtector())
    return db, env, ModelRouter(db, SimpleNamespace(offline=False), credentials=creds)


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db, self.env, self.router = make_router(self.tmp.name)
        self.admin = ModelAdmin(self.router)

    def tearDown(self): self.tmp.cleanup()

    def test_preset_account_brings_its_models_and_hides_key(self):
        account = self.admin.add_account('deepseek', key='sk-secret-123')
        self.assertEqual((account.id, account.kind, account.credential), ('deepseek', 'deepseek', 'stored:deepseek'))
        self.assertEqual(sorted(m.model_id for m in self.admin.models()), ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertNotIn('123', self.admin.describe_key(account))
        self.assertNotIn('sk-secret', json.dumps(self.db.get_setting('model_config')))      # 配置里只有引用
        second = self.admin.add_account('deepseek', key='another')                           # 第二个同类账户
        self.assertEqual(second.id, 'deepseek-2')

    def test_account_validation(self):
        with self.assertRaisesRegex(ValueError, 'Key'): self.admin.add_account('deepseek')
        with self.assertRaisesRegex(ValueError, 'https'): self.admin.add_account('', label='x', base_url='http://evil.example', key='k')
        # Codex 审计 R5：前缀像回环地址的外部主机不能用 http
        for bad in ('http://127.0.0.1.attacker.invalid/v1', 'http://127.0.0.1-example.invalid/v1', 'http://localhost.evil/v1'):
            with self.subTest(url=bad), self.assertRaisesRegex(ValueError, 'https'):
                self.admin.add_account('', label='x', base_url=bad, key='k')
        for bad in ('https://x.example/v1?key=1', 'https://x.example/v1#a', 'https:///v1', 'http://127.0.0.1:99999/v1'):
            with self.subTest(url=bad), self.assertRaises(ValueError): self.admin.add_account('', label='x', base_url=bad, key='k')
        for good in ('http://127.0.0.1:8000/v1', 'http://127.8.0.2/v1', 'http://[::1]:9000/v1'):
            self.assertEqual(self.admin.add_account('', label='本机', base_url=good, key='k').base_url, good)
        with self.assertRaisesRegex(ValueError, '用户名'): self.admin.add_account('', label='x', base_url='https://u:p@x.example', key='k')
        with self.assertRaisesRegex(ValueError, '环境变量'): self.admin.add_account('deepseek', key_env='BAD NAME')
        env_account = self.admin.add_account('deepseek', key_env='MY_DS_KEY')
        self.assertEqual(env_account.credential, 'env:MY_DS_KEY')

    def test_editing_address_moves_key_and_old_snapshots_cannot_use_it(self):
        # Codex 审计 R1：账户改地址（换或不换 Key）后，拿着旧地址快照的请求取不到 Key
        from aicompanion.providers.router import ModelUnavailable
        account = self.admin.add_account('', label='兼容', base_url='https://old.invalid/v1', key='OLD')
        self.admin.add_model(account.id, 'm', ('chat',)); self.admin.choose('chat', None, f'{account.id}/m')
        old = self.router.resolve('chat', 'deepseek-whale')
        self.admin.update_account(account.id, base_url='https://new.invalid/v1')               # 只改地址，不换 Key
        self.assertEqual(self.router.secret(old), '')
        with self.assertRaises(ModelUnavailable): self.router.client_for(old)
        middle = self.router.resolve('chat', 'deepseek-whale')
        self.assertEqual(self.router.secret(middle), 'OLD')
        self.admin.update_account(account.id, base_url='https://third.invalid/v1', key='NEW')  # 改地址并换 Key
        self.assertEqual((self.router.secret(old), self.router.secret(middle)), ('', ''))
        new = self.router.resolve('chat', 'deepseek-whale')
        self.assertEqual((self.router.secret(new), new.base_url), ('NEW', 'https://third.invalid/v1'))

    def test_blank_key_on_edit_keeps_old_key_and_new_key_rotates(self):
        account = self.admin.add_account('deepseek', key='first')
        self.admin.update_account(account.id, label='主账户', key='')
        self.assertEqual(self.router.credentials.get(account.credential), 'first')
        self.admin.update_account(account.id, key='second')
        self.assertEqual(self.router.credentials.get(account.credential), 'second')
        self.assertEqual(self.admin.accounts()[0].label, '主账户')

    def test_choose_checks_capabilities_and_routes(self):
        ds = self.admin.add_account('deepseek', key='k1')
        mm = self.admin.add_account('minimax-cn', key='k2')
        bz = self.admin.add_account('breezeblue', key='k3')
        self.admin.choose('chat', None, f'{ds.id}/deepseek-flash')
        with self.assertRaisesRegex(ValueError, '识图'): self.admin.choose('vision', None, f'{ds.id}/deepseek-v4-pro')
        self.admin.choose('vision', 'deepseek-whale', f'{mm.id}/MiniMax-M3')
        with self.assertRaisesRegex(ValueError, '暂未接通'):                                # Codex 审计 R3：MiniMax 办事尚未接通
            self.admin.choose('agent', None, f'{mm.id}/MiniMax-M3')
        self.assertEqual(agent_route(next(a for a in self.admin.accounts() if a.id == mm.id)), 'minimax-cn')   # 国内站对应 minimax-cn 路由
        self.assertNotIn(f'{bz.id}/breeze-tts-2', [m.id for m in self.admin.models('agent')])
        with self.assertRaisesRegex(ValueError, '音色'): self.admin.choose('tts', None, f'{bz.id}/breeze-tts-2')
        self.admin.choose('tts', 'deepseek-whale', f'{bz.id}/breeze-tts-2', voice='voc_whale', language='zh', speed=1.2)
        self.assertEqual(self.router.tts_target('deepseek-whale').voice, 'voc_whale')
        self.admin.choose('search', None, backend='deepseek', provider=ds.id)
        self.assertEqual(self.router.search_target('x').api_key, 'k1')
        with self.assertRaisesRegex(ValueError, 'DeepSeek'): self.admin.choose('search', None, backend='deepseek', provider=mm.id)
        self.admin.choose('vision', 'deepseek-whale', '')                                   # 角色清除 = 跟随全局
        self.assertIsNone(self.admin.binding('vision', 'deepseek-whale'))

    def test_account_in_use_cannot_be_removed_and_removal_drops_key(self):
        ds = self.admin.add_account('deepseek', key='k1')
        self.admin.choose('chat', None, f'{ds.id}/deepseek-flash')
        with self.assertRaises(ValueError): self.admin.remove_account(ds.id)
        self.admin.choose('chat', None, '')
        self.admin.remove_account(ds.id)
        self.assertEqual(self.router.credentials.get('stored:deepseek'), '')
        self.assertEqual(self.admin.models(), [])

    def test_custom_model(self):
        account = self.admin.add_account('', label='本机服务', base_url='http://127.0.0.1:8000/v1', key='k')
        self.admin.add_model(account.id, 'qwen-local', ('chat', 'vision'))
        self.assertEqual([m.model_id for m in self.admin.models('vision')], ['qwen-local'])
        self.assertIsNone(agent_route(account))                                            # 未验证的服务不能办事
        with self.assertRaises(ValueError): self.admin.add_model(account.id, ' ')
        with self.assertRaises(ValueError): self.admin.add_model(account.id, 'x', ('teleport',))

    def test_same_named_accounts_are_distinguishable(self):
        a = self.admin.add_account('deepseek', key='k1'); b = self.admin.add_account('deepseek', key='k2')
        self.assertEqual((a.label, b.label), ('DeepSeek', 'DeepSeek 2'))
        self.admin.update_account(b.id, label='DeepSeek')                                # 改回重名也会自动加编号
        self.assertEqual(self.admin.accounts()[1].label, 'DeepSeek 2')
        labels = [self.admin.model_label(m) for m in self.admin.models('chat')]
        self.assertEqual(len(labels), len(set(labels)))

    def test_agent_lists_only_harness_models_and_explains_scope(self):
        from aicompanion.providers.models import Binding
        mm = self.admin.add_account('minimax-cn', key='k')
        self.admin.add_model(mm.id, 'MiniMax-M3.1-Flash', ('chat', 'tools'))
        self.assertNotIn('MiniMax-M3.1-Flash', [m.model_id for m in self.admin.models('agent')])
        with self.assertRaisesRegex(ValueError, '暂未接通'):
            self.admin.choose('agent', None, f'{mm.id}/MiniMax-M3.1-Flash')
        self.assertIn('还没有配置办事模型', self.router.agent_problem('deepseek-whale'))
        # 旧配置里留下的不支持模型：不启用，提示说清是哪一层设置
        self.router.store.bind(Binding('agent', f'{mm.id}/MiniMax-M3.1-Flash', route='minimax-cn'))
        self.assertIsNone(self.router.agent_target('deepseek-whale'))
        self.assertIn('全局默认', self.router.agent_problem('deepseek-whale'))
        # MiniMax 办事暂未接通（Codex 审计 R3）：下拉里没有，提示写明只能用 DeepSeek
        self.assertEqual(self.admin.models('agent'), [])
        self.router.store.bind(Binding('agent', f'{mm.id}/MiniMax-M3', route='minimax-cn'))
        self.assertIsNone(self.router.agent_target('deepseek-whale'))
        self.assertIn('暂未接通', self.router.agent_problem('deepseek-whale'))
        ds = self.admin.add_account('deepseek', key='kd')
        self.admin.choose('agent', 'deepseek-whale', f'{ds.id}/deepseek-flash')
        target = self.router.agent_target('deepseek-whale')
        self.assertEqual((target.model_id, target.source), ('deepseek-flash', '角色单独设置'))
        self.assertEqual(self.router.agent_problem('deepseek-whale'), '')

    def test_test_chat_uses_configured_client(self):
        ds = self.admin.add_account('deepseek', key='k1')
        self.admin.choose('chat', None, f'{ds.id}/deepseek-flash')
        fake = SimpleNamespace(generate_reply_stream=lambda h, p, c: iter(['收到']))
        self.router._factory = lambda resolved, secret: fake
        self.assertEqual(self.admin.test_chat('deepseek-whale'), 'DeepSeek · deepseek-flash：收到')
        self.router._clients.clear(); self.admin.choose('chat', None, '')
        with self.assertRaises(ValueError): self.admin.test_chat('deepseek-whale')


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\0\0' * 2400)
    return out.getvalue()


@unittest.skipIf(QApplication is None, '需要 PySide6')
class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db, self.env, self.router = make_router(self.tmp.name)
        from aicompanion.ui.model_page import ModelPage
        sync = lambda function, ok, fail: ok(function(None))   # 测试里同步执行，不开线程
        self.page = ModelPage(self.router, [('deepseek-whale', '鲸娘'), ('custom-1', '自建')], 'deepseek-whale', run=sync)
        self.admin = self.page.admin

    def tearDown(self):
        self.page.deleteLater(); self.tmp.cleanup()

    def test_global_and_character_scopes(self):
        ds = self.admin.add_account('deepseek', key='k1'); bz = self.admin.add_account('breezeblue', key='k2')
        self.page.refresh()
        self.assertEqual(self.page.table.rowCount(), 2)
        self.assertNotIn('k1', ''.join(self.page.table.item(r, 3).text() for r in range(2)))
        picker = self.page.pickers['chat']
        picker.setCurrentIndex(picker.findData(f'{ds.id}/deepseek-flash'))
        self.page.pickers['search'].setCurrentIndex(self.page.pickers['search'].findData('wikipedia'))
        self.assertTrue(self.page.apply())
        self.assertEqual(self.router.resolve('chat', 'custom-1').source, 'global')
        # 切到鲸娘：单独设置音色；聊天保持“跟随全局”
        self.page.scope.setCurrentIndex(self.page.scope.findData('deepseek-whale'))
        self.assertEqual(self.page.pickers['chat'].currentText(), '跟随全局')
        tts = self.page.pickers['tts']; tts.setCurrentIndex(tts.findData(f'{bz.id}/breeze-tts-2'))
        self.page.voice.setText('voc_whale'); self.page.speed.setValue(1.25)
        self.assertTrue(self.page.apply())
        self.assertEqual((self.router.tts_target('deepseek-whale').voice, self.router.tts_target('deepseek-whale').speed), ('voc_whale', 1.25))
        self.assertIsNone(self.router.tts_target('custom-1'))
        # 重新载入范围时回显已保存的值
        self.page.scope.setCurrentIndex(0); self.page.scope.setCurrentIndex(self.page.scope.findData('deepseek-whale'))
        self.assertEqual((self.page.voice.text(), self.page.speed.value()), ('voc_whale', 1.25))

    def test_invalid_choice_is_reported_and_nothing_saved(self):
        bz = self.admin.add_account('breezeblue', key='k'); self.page.refresh()
        tts = self.page.pickers['tts']; tts.setCurrentIndex(tts.findData(f'{bz.id}/breeze-tts-2'))
        self.page.voice.setText('')
        with mock.patch.object(QMessageBox, 'warning') as warning:
            self.assertFalse(self.page.apply())
        warning.assert_called_once()
        self.assertIsNone(self.admin.binding('tts', None))

    def test_choices_save_without_apply(self):
        # 实机反馈：选完不点“应用”就切换范围或关闭设置，选择会丢
        ds = self.admin.add_account('deepseek', key='k1'); bz = self.admin.add_account('breezeblue', key='k2'); self.page.refresh()
        chat = self.page.pickers['chat']; chat.setCurrentIndex(chat.findData(f'{ds.id}/deepseek-flash'))
        self.assertEqual(self.admin.binding('chat', None).model, f'{ds.id}/deepseek-flash')
        self.page.scope.setCurrentIndex(self.page.scope.findData('deepseek-whale'))     # 切换范围不会改动全局
        self.assertEqual(self.admin.binding('chat', None).model, f'{ds.id}/deepseek-flash')
        self.assertIsNone(self.admin.binding('chat', 'deepseek-whale'))
        tts = self.page.pickers['tts']
        with mock.patch.object(QMessageBox, 'warning') as warning:
            tts.setCurrentIndex(tts.findData(f'{bz.id}/breeze-tts-2'))                   # 还没填音色：只提示，不弹窗
        warning.assert_not_called()
        self.assertIn('音色', self.page.status.text())
        self.page.voice.setText('voc_whale'); self.page.voice.editingFinished.emit()
        self.assertEqual(self.router.tts_target('deepseek-whale').voice, 'voc_whale')
        self.page.speed.setValue(1.3)
        self.assertEqual(self.router.tts_target('deepseek-whale').speed, 1.3)
        self.page.scope.setCurrentIndex(0)
        self.page.scope.setCurrentIndex(self.page.scope.findData('deepseek-whale'))
        self.assertEqual(self.page.pickers['chat'].currentText(), '跟随全局')           # 载入时不误存
        self.assertEqual(self.page.voice.text(), 'voc_whale')

    def test_tts_test_reports_duration(self):
        bz = self.admin.add_account('breezeblue', key='k'); self.page.refresh()
        tts = self.page.pickers['tts']; tts.setCurrentIndex(tts.findData(f'{bz.id}/breeze-tts-2')); self.page.voice.setText('voc_x')
        played = []; self.page.speech_output = SimpleNamespace(play_audio=lambda text, raw: played.append(raw))
        audio = wav()
        with mock.patch('aicompanion.providers.admin.ModelAdmin.test_tts', return_value=audio) as synth:
            self.page.test_tts()
        self.assertIn('0.1 秒', self.page.status.text())
        synth.assert_called_once(); self.assertEqual(played, [audio])        # Codex 审计 R6：只合成一次，播放的就是这份


if __name__ == '__main__':
    unittest.main()
