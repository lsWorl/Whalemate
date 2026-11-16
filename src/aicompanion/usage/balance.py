"""服务商账户余额（T29）：只查询官方提供余额接口的服务商，结果只在本机显示。

资料核对 2026-11-16：
- DeepSeek：GET https://api.deepseek.com/user/balance，Bearer 鉴权，返回 is_available 和按币种的 balance_infos[].total_balance
  （https://api-docs.deepseek.com/zh-cn/api/get-user-balance）。
- Kimi（月之暗面）：GET https://api.moonshot.cn/v1/users/me/balance，Bearer 鉴权，返回 data.available_balance（元）
  （https://platform.kimi.com/docs/api/balance）。
- MiniMax、智谱、百炼：没有找到用 API Key 查询余额的官方接口，不查询，界面提示去控制台看。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urlsplit
from urllib.request import Request

from aicompanion.net import open_url


@dataclass(frozen=True)
class Balance:
    label: str                  # 服务商在模型页里的名字
    amount: float | None        # 可用余额；查询失败时为 None
    currency: str = 'CNY'
    available: bool = True      # 服务商说余额是否还够调用
    error: str = ''             # 不含密钥和地址的简短原因

    def text(self) -> str:
        if self.amount is None:
            return f'{self.label} 余额查询失败（{self.error}）' if self.error else f'{self.label} 余额未知'
        unit = '元' if self.currency == 'CNY' else f' {self.currency}'
        return f'{self.label} 余额 {self.amount:.2f}{unit}' + ('' if self.available else '（已不足以调用）')


def _deepseek(data) -> tuple[float, str, bool]:
    infos = data.get('balance_infos') or []
    info = next((i for i in infos if i.get('currency') == 'CNY'), infos[0] if infos else None)
    if info is None:
        raise ValueError('没有余额信息')
    return float(info['total_balance']), str(info.get('currency') or 'CNY'), bool(data.get('is_available', True))


def _moonshot(data) -> tuple[float, str, bool]:
    amount = float(data['data']['available_balance'])
    return amount, 'CNY', amount > 0


def endpoint(provider) -> tuple[str, object] | None:
    """返回 (余额地址, 解析函数)；不支持的服务商返回 None。只认官方域名，自定义中转地址不查询。"""
    host = (urlsplit(provider.base_url or '').hostname or '').lower()
    if provider.kind == 'deepseek' or host == 'api.deepseek.com':
        return 'https://api.deepseek.com/user/balance', _deepseek
    if host in ('api.moonshot.cn', 'api.moonshot.ai'):
        return f'https://{host}/v1/users/me/balance', _moonshot
    return None


def supported(config) -> list:
    """配置里能查余额、且填了凭据的服务商；同一地址和凭据只查一次。"""
    seen, result = set(), []
    for provider in config.providers.values():
        target = endpoint(provider)
        if target is None or not provider.credential:
            continue
        key = (target[0], provider.credential)
        if key in seen:
            continue
        seen.add(key); result.append(provider)
    return result


def query(router, timeout=10, opener=open_url) -> list[Balance]:
    """逐个查询余额；单个服务商失败不影响其他。离线模式下不联网，返回空列表。"""
    if getattr(router, 'offline', False):
        return []
    results = []
    for provider in supported(router.store.load()):
        url, parse = endpoint(provider)
        try:
            key = router.credentials.get(provider.credential, endpoint=provider.base_url)
            if not key:
                results.append(Balance(provider.label, None, error='没有可用的 Key')); continue
            request = Request(url, headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'})
            with opener(request, timeout=timeout) as response:
                amount, currency, available = parse(json.loads(response.read(65536).decode('utf-8')))
            results.append(Balance(provider.label, amount, currency, available))
        except Exception as error:   # 网络、鉴权、格式问题都只显示简短原因
            code = getattr(error, 'code', None)
            reason = {401: 'Key 无效', 403: '没有权限'}.get(code, f'HTTP {code}' if code else type(error).__name__)
            results.append(Balance(provider.label, None, error=reason))
    return results
