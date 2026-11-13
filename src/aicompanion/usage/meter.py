"""词元计量：每次模型请求结束时，客户端把接口返回的用量（或估算）交给这里，按"用途"记账。

用途由调用方用 `with using('memory'):` 声明，默认是陪伴聊天。用途存在 ContextVar 里，
后台线程各自独立，不会串。价目表单位是人民币元 / 百万词元，可在设置里改；数字只是本地估算，以账单为准。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import json
import threading

PURPOSES = {
    'companion': '陪伴聊天', 'agent': '干活', 'memory': '记忆整理', 'purpose': '心事', 'life': '自己的生活', 'voice': '语音翻译',
    'qq': 'QQ', 'bili': 'B站', 'embedding': '记忆向量',
}
# 默认价目（元 / 百万词元）。各家会调价，请在设置里按官网更新。
DEFAULT_PRICES = {
    'text-embedding-v4': {'input': 0.5, 'output': 0},
    'embedding-3': {'input': 0.5, 'output': 0},
    'qwen3.7-text-embedding': {'input': 0.5, 'output': 0},
    'qwen3.7-text-embedding-flash': {'input': 0.125, 'output': 0},
    'deepseek': {'input': 2.0, 'cached': 0.2, 'output': 3.0},
    'qwen': {'input': 0.8, 'cached': 0.16, 'output': 2.0},
    'glm': {'input': 2.0, 'cached': 0.5, 'output': 8.0},
    'gpt': {'input': 15.0, 'cached': 1.5, 'output': 60.0},
    'claude': {'input': 22.0, 'cached': 2.2, 'output': 110.0},
    'mock': {'input': 0.0, 'cached': 0.0, 'output': 0.0},
    '*': {'input': 2.0, 'cached': 0.5, 'output': 8.0},
}
CHARS_PER_TOKEN = 1.4   # 接口不返回用量时的粗略估算：中文约 1.4 字 / 词元

_purpose: ContextVar[str] = ContextVar('usage_purpose', default='companion')
_character: ContextVar[str] = ContextVar('usage_character', default='')
_ref: ContextVar[str] = ContextVar('usage_ref', default='')
_recorder = None
_lock = threading.Lock()


@contextmanager
def using(purpose: str, character_id: str | None = None, ref: str | None = None):
    tokens = [_purpose.set(purpose)]
    if character_id is not None: tokens.append(_character.set(character_id))
    if ref is not None: tokens.append(_ref.set(ref))
    try: yield
    finally:
        for var, token in zip((_purpose, _character, _ref), tokens): var.reset(token)


def set_recorder(fn):
    """fn(purpose, model, prompt, completion, cached, estimated, character_id, ref)。应用启动时设置；测试可替换。"""
    global _recorder
    _recorder = fn


def estimate_tokens(text_chars: int) -> int:
    return max(1, int(text_chars/CHARS_PER_TOKEN))


def report(model: str, usage: dict | None, prompt_chars: int = 0, completion_chars: int = 0):
    """客户端在一次请求结束后调用。usage 为接口返回的 usage 字段；为空时按字数估算。"""
    estimated = not usage
    if usage:
        prompt = int(usage.get('prompt_tokens') or 0); completion = int(usage.get('completion_tokens') or 0)
        details = usage.get('prompt_tokens_details') or {}
        cached = int(usage.get('prompt_cache_hit_tokens') or details.get('cached_tokens') or 0)
    else:
        prompt = estimate_tokens(prompt_chars); completion = estimate_tokens(completion_chars) if completion_chars else 0; cached = 0
    fn = _recorder
    if fn:
        with _lock:
            try: fn(_purpose.get(), model or '', prompt, completion, cached, estimated, _character.get(), _ref.get())
            except Exception: pass   # 记账失败不能影响聊天


# ---- 价目 -----------------------------------------------------------------------
def price_for(model: str, table: dict | None = None) -> dict:
    table = table or DEFAULT_PRICES
    name = (model or '').lower()
    if name in table: return table[name]
    for key in sorted(table, key=len, reverse=True):
        if key != '*' and key in name: return table[key]
    return table.get('*', DEFAULT_PRICES['*'])


def cost_of(model, prompt, completion, cached=0, table=None) -> float:
    p = price_for(model, table)
    fresh = max(0, prompt-cached)
    return (fresh*p['input']+cached*p.get('cached', p['input'])+completion*p['output'])/1_000_000


def load_prices(database) -> dict:
    raw = database.get_setting('price_table')
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except ValueError: raw = None
    table = dict(DEFAULT_PRICES)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict) and all(isinstance(v.get(x), (int, float)) for x in ('input', 'output')): table[k.lower()] = v
    return table


def today_start(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime('%Y-%m-%d 00:00:00')


def database_recorder(database):
    """把用量写进数据库，成本按当时的价目表算好再存。"""
    def record(purpose, model, prompt, completion, cached, estimated, character_id, ref):
        cost = cost_of(model, prompt, completion, cached, load_prices(database))
        database.add_token_usage(purpose, model, prompt, completion, cached, cost, estimated, character_id, ref)
    return record


# ---- 额度 -----------------------------------------------------------------------
class Quota:
    """每日额度（元）：可设总额，也可分用途设。80% 提醒一次，100% 该用途停止（总额到了全部停止）。"""
    WARN = 0.8

    def __init__(self, database):
        self.database = database; self.warned = set()

    def limits(self) -> dict:
        limits = {'*': float(self.database.get_setting('quota_daily_total', 0) or 0)}
        for p in PURPOSES:
            value = float(self.database.get_setting(f'quota_daily:{p}', 0) or 0)
            if value > 0: limits[p] = value
        return limits

    def spent(self, now=None) -> dict:
        rows = self.database.token_usage_by_purpose(today_start(now))
        spent = {r['purpose']: float(r['cost'] or 0) for r in rows}
        spent['*'] = sum(spent.values())
        return spent

    def check(self, purpose: str, now=None) -> tuple[str, str]:
        """返回 (状态, 说明)。状态：ok / warn（刚过 80%，只提示一次）/ stop。"""
        limits = self.limits(); spent = self.spent(now)
        day = today_start(now)[:10]
        worst = ('ok', '')
        for key in (purpose, '*'):
            limit = limits.get(key, 0)
            if limit <= 0: continue
            used = spent.get(key, 0.0); name = '今日总额' if key == '*' else f'今日{PURPOSES.get(key, key)}额度'
            if used >= limit: return 'stop', f'{name} {limit:.2f} 元已用完（已用 {used:.2f} 元）'
            if used >= limit*self.WARN and (day, key) not in self.warned:
                self.warned.add((day, key)); worst = ('warn', f'{name}已用 {used/limit:.0%}（{used:.2f}/{limit:.2f} 元）')
        return worst

    def allowed(self, purpose: str, now=None) -> bool:
        return self.check_silent(purpose, now) != 'stop'

    def check_silent(self, purpose: str, now=None) -> str:
        limits = self.limits(); spent = self.spent(now)
        for key in (purpose, '*'):
            limit = limits.get(key, 0)
            if limit > 0 and spent.get(key, 0.0) >= limit: return 'stop'
        return 'ok'
