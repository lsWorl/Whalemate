"""T29：今日额度余量与服务商余额查询。全部用假数据和假网络，不联网。"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from aicompanion.providers.models import ProviderProfile
from aicompanion.providers.store import ModelConfig
from aicompanion.storage.database import Database
from aicompanion.usage import balance, meter


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def router(providers, keys, offline=False):
    config = ModelConfig(providers={p.id: p for p in providers})
    return SimpleNamespace(offline=offline, store=SimpleNamespace(load=lambda: config),
                           credentials=SimpleNamespace(get=lambda ref, endpoint=None: keys.get(ref, '')))


DEEPSEEK = ProviderProfile('deepseek', 'DeepSeek', 'deepseek', 'https://api.deepseek.com', credential='stored:ds')
KIMI = ProviderProfile('kimi', 'Kimi', 'openai_compatible', 'https://api.moonshot.cn/v1', credential='stored:kimi')
MINIMAX = ProviderProfile('minimax-cn', 'MiniMax', 'minimax', 'https://api.minimaxi.com/v1', credential='stored:mm')
RELAY = ProviderProfile('relay', '中转', 'openai_compatible', 'https://relay.example/v1', credential='stored:relay')


class BalanceTests(unittest.TestCase):
    def test_only_official_endpoints_are_queried(self):
        self.assertEqual(balance.endpoint(DEEPSEEK)[0], 'https://api.deepseek.com/user/balance')
        self.assertEqual(balance.endpoint(KIMI)[0], 'https://api.moonshot.cn/v1/users/me/balance')
        self.assertIsNone(balance.endpoint(MINIMAX))
        self.assertIsNone(balance.endpoint(RELAY))
        # 种类是 DeepSeek 但地址是中转：Key 属于中转站，不能发给 DeepSeek 官方。
        self.assertIsNone(balance.endpoint(ProviderProfile('ds-relay', '中转', 'deepseek', 'https://relay.example/v1', credential='x')))
        self.assertIsNone(balance.endpoint(ProviderProfile('ds-http', 'DS', 'deepseek', 'http://api.deepseek.com', credential='x')))
        no_key = ProviderProfile('d2', 'DeepSeek 2', 'deepseek', 'https://api.deepseek.com')
        duplicate = ProviderProfile('d3', 'DeepSeek 3', 'deepseek', 'https://api.deepseek.com', credential='stored:ds')
        config = ModelConfig(providers={p.id: p for p in (DEEPSEEK, no_key, duplicate, MINIMAX)})
        self.assertEqual([p.id for p in balance.supported(config)], ['deepseek'])

    def test_query_parses_and_isolates_failures(self):
        seen = []
        def opener(request, timeout=None):
            seen.append((request.full_url, request.get_header('Authorization')))
            if 'deepseek' in request.full_url:
                body = {'is_available': True, 'balance_infos': [
                    {'currency': 'USD', 'total_balance': '1.00'}, {'currency': 'CNY', 'total_balance': '12.34'}]}
                return FakeResponse(json.dumps(body).encode())
            raise HTTPError(request.full_url, 401, 'unauthorized', {}, None)
        results = balance.query(router([DEEPSEEK, KIMI, MINIMAX], {'stored:ds': 'k1', 'stored:kimi': 'k2'}), opener=opener)
        self.assertEqual([r.text() for r in results], ['DeepSeek 余额 12.34元', 'Kimi 余额查询失败（Key 无效）'])
        self.assertEqual(seen[0], ('https://api.deepseek.com/user/balance', 'Bearer k1'))
        self.assertNotIn('k2', results[1].text())

    def test_kimi_and_unavailable_balance(self):
        opener = lambda request, timeout=None: FakeResponse(json.dumps({'data': {'available_balance': 0}}).encode())
        result, = balance.query(router([KIMI], {'stored:kimi': 'k'}), opener=opener)
        self.assertEqual(result.text(), 'Kimi 余额 0.00元（已不足以调用）')

    def test_kimi_global_site_reports_usd(self):
        global_kimi = ProviderProfile('kimi-global', 'Kimi 国际站', 'openai_compatible', 'https://api.moonshot.ai/v1', credential='stored:kg')
        seen = []
        def opener(request, timeout=None):
            seen.append(request.full_url)
            return FakeResponse(json.dumps({'data': {'available_balance': 3.5}}).encode())
        result, = balance.query(router([global_kimi], {'stored:kg': 'k'}), opener=opener)
        self.assertEqual(seen, ['https://api.moonshot.ai/v1/users/me/balance'])
        self.assertEqual(result.text(), 'Kimi 国际站 余额 3.50 USD')

    def test_offline_and_missing_key_do_not_call_network(self):
        def opener(*args, **kwargs):
            raise AssertionError('不应联网')
        self.assertEqual(balance.query(router([DEEPSEEK], {'stored:ds': 'k'}, offline=True), opener=opener), [])
        result, = balance.query(router([DEEPSEEK], {}), opener=opener)
        self.assertEqual(result.text(), 'DeepSeek 余额查询失败（没有可用的 Key）')


class QuotaRemainingTests(unittest.TestCase):
    def test_remaining_reports_limits_and_never_goes_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder)/'t.sqlite3'); db.initialize()
            quota = meter.Quota(db)
            self.assertEqual(quota.remaining(), {})
            db.set_setting('quota_daily_total', 5)
            db.set_setting('quota_daily:qq', 1)
            spent = {'*': 6.5, 'qq': 0.25}
            quota.spent = lambda now=None: spent
            self.assertEqual(quota.remaining(), {'*': (5.0, 6.5, 0.0), 'qq': (1.0, 0.25, 0.75)})


if __name__ == '__main__':
    unittest.main()
