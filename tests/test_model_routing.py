"""计划书 v4 功能一：模型配置、凭据与按能力解析。纯 Python，不联网（重定向测试只用本机端口）。"""
import base64
import http.server
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aicompanion.llm.openai_compatible_client import LLMResponseError, OpenAICompatibleClient
from aicompanion.providers.credentials import CredentialError, CredentialStore
from aicompanion.providers.models import MODEL_PRESETS, PROVIDER_PRESETS, Binding, ModelProfile, ProviderProfile
from aicompanion.providers.router import ModelRouter, ModelUnavailable, thinking_params
from aicompanion.providers.store import CONFIG_KEY, ModelConfigStore
from aicompanion.storage.database import Database


class FakeProtector:
    """代替 DPAPI：可逆但不是明文，用来确认数据库里存的不是 Key 本身。"""
    name = 'fake'

    def protect(self, data): return base64.b64encode(data[::-1])

    def unprotect(self, data): return base64.b64decode(data)[::-1]


class Recorder:
    """记下工厂收到的参数，代替真实客户端。"""
    def __init__(self): self.made = []

    def __call__(self, resolved, secret):
        client = SimpleNamespace(resolved=resolved, secret=secret)
        self.made.append(client)
        return client


class RoutingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'test.sqlite3'); self.db.initialize()
        self.env = {}
        self.creds = CredentialStore(self.db, self.env, protector=FakeProtector())
        self.factory = Recorder()
        self.router = ModelRouter(self.db, SimpleNamespace(offline=False), credentials=self.creds, client_factory=self.factory)
        self.store = ModelConfigStore(self.db)

    def tearDown(self): self.tmp.cleanup()

    def add_deepseek(self, key='ds-key'):
        ref = self.creds.save('deepseek-main', key)
        self.store.put_provider(ProviderProfile('deepseek', 'DeepSeek', 'deepseek', 'https://api.deepseek.com', credential=ref))
        self.store.put_model(MODEL_PRESETS['deepseek-flash'])
        return ref

    def add_minimax(self, key='mm-key'):
        ref = self.creds.save('minimax-main', key)
        self.store.put_provider(ProviderProfile('minimax-global', 'MiniMax', 'minimax', 'https://api.minimax.io/v1', credential=ref))
        self.store.put_model(MODEL_PRESETS['minimax-m3.1-flash'])
        self.store.put_model(MODEL_PRESETS['minimax-m3'])
        return ref


class LayerTests(RoutingBase):
    def test_unconfigured_is_offline_and_never_reads_env(self):
        self.env.update({'LLM_API_KEY': 'old', 'DEEPSEEK_API_KEY': 'old'})   # 旧 .env 不再被读取
        client, resolved = self.router.client('chat', 'deepseek-whale')
        self.assertIs(client, self.router.mock()); self.assertIsNone(resolved)
        self.assertEqual(self.factory.made, [])
        self.assertIn('未配置', self.router.label('deepseek-whale'))
        self.assertFalse(self.router.ready('chat', 'deepseek-whale'))
        with self.assertRaises(ModelUnavailable): self.router.resolve('vision', 'deepseek-whale')
        self.assertEqual(self.router.search_target('deepseek-whale').backend, 'wikipedia')
        self.assertIsNone(self.router.agent_target('deepseek-whale'))

    def test_character_binding_beats_global(self):
        self.add_deepseek(); self.add_minimax()
        self.store.bind(Binding('chat', 'minimax-m3'))
        self.store.bind(Binding('chat', 'deepseek-flash', character_id='deepseek-whale'))
        whale = self.router.resolve('chat', 'deepseek-whale')
        self.assertEqual((whale.source, whale.model_id, whale.label), ('character', 'deepseek-flash', 'DeepSeek · deepseek-flash'))
        self.assertEqual(self.router.resolve('chat', 'emilia').model_id, 'MiniMax-M3')
        self.store.unbind('chat', 'deepseek-whale')                                          # 恢复继承
        self.assertEqual(self.router.resolve('chat', 'deepseek-whale').source, 'global')

    def test_snapshot_is_frozen(self):
        self.add_deepseek(); self.store.bind(Binding('chat', 'deepseek-flash'))
        before = self.router.resolve('chat', 'emilia')
        self.add_minimax(); self.store.bind(Binding('chat', 'minimax-m3'))
        self.assertEqual(before.model_id, 'deepseek-flash')          # 已拿到的快照不受新设置影响
        self.assertGreater(self.router.resolve('chat', 'emilia').config_revision, before.config_revision)

    def test_missing_key_does_not_fall_back(self):
        self.add_minimax()
        self.store.bind(Binding('chat', 'minimax-m3'))
        self.creds.delete('minimax-main')
        with self.assertRaises(ModelUnavailable):
            self.router.client('chat', 'emilia')                     # 不偷偷换到别的服务，也不退回离线
        self.assertFalse(self.router.ready('chat', 'emilia'))

    def test_offline_mode_never_builds_clients(self):
        self.add_deepseek(); self.store.bind(Binding('chat', 'deepseek-flash'))
        router = ModelRouter(self.db, SimpleNamespace(offline=True), credentials=self.creds, client_factory=self.factory)
        client, _ = router.client('chat', 'emilia')
        self.assertEqual(self.factory.made, []); self.assertEqual(router.label('emilia'), '离线模拟模型')
        self.assertIsNone(router.agent_target())

    def test_vision_binding_is_independent(self):
        self.add_deepseek(); self.add_minimax()
        self.store.bind(Binding('chat', 'deepseek-flash')); self.store.bind(Binding('vision', 'minimax-m3'))
        vision = self.router.resolve('vision', 'emilia', 'avatar')
        self.assertEqual((vision.model_id, vision.credential, vision.temperature), ('MiniMax-M3', 'stored:minimax-main', 0.0))
        self.assertEqual(self.router.resolve('chat', 'emilia').credential, 'stored:deepseek-main')   # 互不影响


class CacheTests(RoutingBase):
    def test_key_change_rebuilds_client(self):
        self.add_deepseek('first'); self.store.bind(Binding('chat', 'deepseek-flash'))
        first, _ = self.router.client('chat', 'emilia')
        self.assertIs(self.router.client('chat', 'emilia')[0], first)       # 同样的配置复用实例
        self.creds.save('deepseek-main', 'second')                            # 同地址同模型换 Key
        second, resolved = self.router.client('chat', 'emilia')
        self.assertIsNot(second, first); self.assertEqual(second.secret, 'second')
        self.assertEqual(resolved.credential_revision, 'stored:2')
        self.assertNotIn(first, self.router._clients.values())               # 旧凭据的实例已丢弃

    def test_env_reference_change_rebuilds_client(self):
        # 用户也可以选择让 Key 来自某个环境变量
        self.store.put_provider(ProviderProfile('deepseek', 'DeepSeek', 'deepseek', 'https://api.deepseek.com', credential='env:MY_DS'))
        self.store.put_model(MODEL_PRESETS['deepseek-flash']); self.store.bind(Binding('chat', 'deepseek-flash'))
        self.env['MY_DS'] = 'one'
        first, _ = self.router.client('chat', 'emilia')
        self.env['MY_DS'] = 'rotated'
        second, _ = self.router.client('chat', 'emilia')
        self.assertIsNot(first, second); self.assertEqual(second.secret, 'rotated')

    def test_purpose_parameters_do_not_share_instances(self):
        self.add_minimax(); self.store.bind(Binding('chat', 'minimax-m3.1-flash')); self.store.bind(Binding('vision', 'minimax-m3.1-flash'))
        chat, _ = self.router.client('chat', 'emilia', 'companion')
        vision, _ = self.router.client('vision', 'emilia', 'vision')
        self.assertIsNot(chat, vision)
        self.assertEqual(vision.resolved.max_output, 4000)


class TargetTests(RoutingBase):
    def test_search_binding(self):
        self.add_deepseek('search-key'); self.add_minimax()
        self.store.bind(Binding('search', backend='deepseek', provider='deepseek'))
        self.store.bind(Binding('chat', 'minimax-m3'))                       # 聊天换成 MiniMax 也不改搜索
        target = self.router.search_target('emilia')
        self.assertEqual((target.backend, target.api_key), ('deepseek', 'search-key'))
        self.store.bind(Binding('search', backend='wikipedia', character_id='emilia'))
        self.assertEqual(self.router.search_target('emilia').backend, 'wikipedia')
        self.assertEqual(self.router.search_target('deepseek-whale').backend, 'deepseek')

    def test_agent_target_only_carries_selected_key(self):
        self.add_deepseek('ds-secret'); self.add_minimax('mm-secret')
        self.store.bind(Binding('agent', 'deepseek-flash', route='deepseek-official'))
        target = self.router.agent_target()
        self.assertEqual((target.route, target.model_id, dict(target.env)), ('deepseek-official', 'deepseek-flash', {'DEEPSEEK_API_KEY': 'ds-secret'}))
        self.store.bind(Binding('agent', 'minimax-m3', route='minimax'))
        target = self.router.agent_target()
        self.assertEqual((target.route, target.model_id, dict(target.env)), ('minimax', 'MiniMax-M3', {'MINIMAX_API_KEY': 'mm-secret'}))
        self.creds.delete('minimax-main')
        self.assertIsNone(self.router.agent_target())


class ThinkingTests(unittest.TestCase):
    def test_deepseek_turns_thinking_off_for_chat_and_vision(self):
        model = MODEL_PRESETS['deepseek-flash']
        self.assertEqual(thinking_params('deepseek', model, 'companion'), ({'thinking': {'type': 'disabled'}}, None))
        self.assertEqual(thinking_params('deepseek', model, 'vision'), ({'thinking': {'type': 'disabled'}}, None))
        on = ModelProfile('x', 'deepseek', 'deepseek-flash', thinking={'chat': 'on'})
        self.assertEqual(thinking_params('deepseek', on, 'qq')[0], {'thinking': {'type': 'enabled'}})

    def test_minimax_always_splits_reasoning(self):
        flash, m3 = MODEL_PRESETS['minimax-m3.1-flash'], MODEL_PRESETS['minimax-m3']
        extra, limit = thinking_params('minimax', flash, 'sticker')
        self.assertEqual(extra, {'reasoning_split': True}); self.assertEqual(limit, 4000)   # 关不掉思考：给足额度
        extra, limit = thinking_params('minimax', m3, 'memory')
        self.assertEqual(extra, {'reasoning_split': True, 'thinking': {'type': 'disabled'}}); self.assertIsNone(limit)

    def test_generic_service_gets_no_vendor_parameters(self):
        model = ModelProfile('x', 'p', 'any-model')
        self.assertEqual(thinking_params('openai_compatible', model, 'companion'), ({}, None))
        custom = ModelProfile('x', 'p', 'any-model', max_output={'chat': 800})
        self.assertEqual(thinking_params('openai_compatible', custom, 'qq'), ({}, 800))


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'c.sqlite3'); self.db.initialize()

    def tearDown(self): self.tmp.cleanup()

    def test_stored_key_is_not_plaintext_and_never_described(self):
        creds = CredentialStore(self.db, {}, protector=FakeProtector())
        ref = creds.save('main', 'sk-very-secret-1234')
        self.assertEqual(creds.get(ref), 'sk-very-secret-1234')
        raw = json.dumps(self.db.get_setting('credential:main'))
        self.assertNotIn('sk-very-secret', raw); self.assertNotIn('1234', creds.describe(ref))

    def test_blank_save_keeps_old_key(self):
        creds = CredentialStore(self.db, {}, protector=FakeProtector())
        ref = creds.save('main', 'old')
        with self.assertRaises(CredentialError): creds.save('main', '   ')
        self.assertEqual(creds.get(ref), 'old'); self.assertEqual(creds.revision(ref), 'stored:1')

    def test_no_secure_backend_refuses_to_save(self):
        creds = CredentialStore(self.db, {'X': 'from-env'}, protector=None)
        with self.assertRaises(CredentialError): creds.save('main', 'secret')
        self.assertIsNone(self.db.get_setting('credential:main'))
        self.assertEqual(creds.get('env:X'), 'from-env')                     # 环境变量照常可用

    def test_delete_invalidates(self):
        creds = CredentialStore(self.db, {}, protector=FakeProtector())
        ref = creds.save('main', 'k'); creds.delete('main')
        self.assertEqual(creds.get(ref), ''); self.assertEqual(creds.revision(ref), 'stored:2')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 's.sqlite3'); self.db.initialize()
        self.store = ModelConfigStore(self.db)

    def tearDown(self): self.tmp.cleanup()

    def test_newer_config_is_left_untouched(self):
        future = {'version': 99, 'revision': 7, 'providers': [{'id': 'x', 'label': 'x', 'future_field': 1}]}
        self.db.set_setting(CONFIG_KEY, future)
        self.assertEqual(self.store.load().providers, {})
        self.assertEqual(self.db.get_setting(CONFIG_KEY), future)

    def test_bad_entries_are_skipped(self):
        self.db.set_setting(CONFIG_KEY, {'version': 1, 'providers': [{'label': 'no id'}, {'id': 'ok', 'label': 'OK'}],
                                         'bindings': [{'capability': 'teleport'}, {'capability': 'chat', 'model': 'm'}]})
        config = self.store.load()
        self.assertEqual(list(config.providers), ['ok']); self.assertEqual(len(config.bindings), 1)

    def test_provider_in_use_cannot_be_removed(self):
        self.store.put_provider(PROVIDER_PRESETS['deepseek'])
        self.store.put_model(MODEL_PRESETS['deepseek-flash'])
        self.store.bind(Binding('chat', 'deepseek-flash'))
        with self.assertRaises(ValueError): self.store.remove_provider('deepseek')
        self.store.unbind('chat', None); self.store.remove_provider('deepseek')
        self.assertEqual(self.store.load().models, {})

    def test_revision_increments(self):
        self.store.put_provider(PROVIDER_PRESETS['deepseek'])
        self.store.put_provider(PROVIDER_PRESETS['deepseek'])
        config = self.store.load()
        self.assertEqual(config.revision, 2); self.assertEqual(config.providers['deepseek'].revision, 2)


class ClientPayloadTests(unittest.TestCase):
    def capture(self, client, call):
        sent = {}

        def fake_open(request, timeout=None):
            sent['body'] = json.loads(request.data)
            if sent['body'].get('stream'):
                return io.BytesIO(b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\n')
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}).encode())
        with patch('aicompanion.llm.openai_compatible_client.urlopen', side_effect=fake_open):
            call()
        return sent['body']

    def test_vendor_parameters_reach_every_request(self):
        client = OpenAICompatibleClient('https://x.invalid', 'k', 'm', extra={'thinking': {'type': 'disabled'}}, max_tokens=900)
        body = self.capture(client, lambda: list(client.generate_reply_stream([], 'sys')))
        self.assertEqual((body['thinking'], body['max_tokens']), ({'type': 'disabled'}, 900))
        body = self.capture(client, lambda: client.chat([{'role': 'user', 'content': 'hi'}], max_tokens=300))
        self.assertEqual((body['thinking'], body['max_tokens']), ({'type': 'disabled'}, 300))

    def test_old_constructor_sends_nothing_new(self):
        client = OpenAICompatibleClient('https://x.invalid', 'k', 'm')
        body = self.capture(client, lambda: list(client.generate_reply_stream([], 'sys')))
        self.assertNotIn('max_tokens', body); self.assertNotIn('thinking', body)


class RedirectTests(unittest.TestCase):
    """硬底线：鉴权头不能跟着跳转发到别的地址。用两个本机端口模拟“配置地址”和“跳转目标”。"""

    def serve(self, handler):
        server = http.server.HTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return server

    def test_authorization_is_not_forwarded(self):
        seen = []

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self): seen.append(self.headers.get('Authorization')); self.send_response(200); self.end_headers()
            do_POST = do_GET
            def log_message(self, *args): pass
        target = self.serve(Target)

        for code in (301, 302, 303, 307, 308):
            class Origin(http.server.BaseHTTPRequestHandler):
                def do_POST(self):
                    self.rfile.read(int(self.headers.get('Content-Length') or 0))
                    self.send_response(code); self.send_header('Location', f'http://127.0.0.1:{target.server_port}/steal'); self.end_headers()
                def log_message(self, *args): pass
            origin = self.serve(Origin)
            client = OpenAICompatibleClient(f'http://127.0.0.1:{origin.server_port}', 'secret-key', 'm', timeout_seconds=5)
            with self.assertRaisesRegex(LLMResponseError, f'HTTP {code}'):
                client.chat([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
