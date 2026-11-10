"""T18 联网搜索：查梗 / 黑话的出处和含义，读公开页面。

后端选择（参考 MaiBot web_search 的统一接口）：
- 聊天接口是 DeepSeek 官方（https://api.deepseek.com）时，用 DeepSeek Search & Fetch（`POST /v1/tools/search`、`/v1/tools/fetch`）：
  和聊天同一家、同一个密钥，结果自带给模型读的 summary；每 1000 次成功请求约 0.75 美元，空结果不计费。
- 否则退回中文维基百科的公开 MediaWiki 接口（不用密钥，只读词条导语）。

隐私与费用：
- 只搜词本身（"xx 梗 意思"），不搜聊天原文；`searchable()` 挡掉带长串数字的、已知群友昵称、只有一个人说过的词；
- 每天搜索 / 读页各有上限（设置 community_search_daily / community_fetch_daily，默认 30 / 10），用原子计数；
- 设置 community_web 可整体关掉；测试一律注入假的 opener，不联网。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE = 1024*1024
WIKI = 'https://zh.wikipedia.org/w/api.php'


class SearchError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


@dataclass
class Hit:
    title: str
    url: str
    summary: str
    site: str = ''


def searchable(term: str, *, speakers=0, names=()) -> bool:
    """这个词能不能拿去网上搜：不是号码 / 链接、不是群友昵称、至少两个人用过（单人口头禅、私人外号不外传）。"""
    term = str(term or '').strip()
    if not 2 <= len(term) <= 20: return False
    if re.search(r'\d{5,}|https?://|@', term): return False
    if any(term == n or (len(n) >= 2 and n in term) for n in names if n): return False
    return speakers >= 2


class WebSearch:
    def __init__(self, database, client=None, opener=None, clock=time.time, today=None):
        from datetime import date
        self.db, self.client, self.clock = database, client, clock
        self.today = today or date.today
        self.open = opener or build_opener(NoRedirect()).open
        self.timeout = 20       # 聊天里顺手搜一下时会调短，不让对方干等

    @property
    def enabled(self) -> bool:
        return bool(self.db.get_setting('community_web', True))

    def backend(self) -> str:
        base = str(getattr(self.client, 'base_url', '') or '')
        parts = urlsplit(base)
        return 'deepseek' if parts.scheme == 'https' and parts.hostname == 'api.deepseek.com' and getattr(self.client, 'api_key', '') else 'wikipedia'

    def _budget(self, kind: str) -> bool:
        limit = int(self.db.get_setting(f'community_{kind}_daily', 30 if kind == 'search' else 10) or 0)
        return self.db.reserve_daily(f'community_{kind}_count:{self.today().isoformat()}', limit)

    def used_today(self) -> dict:
        day = self.today().isoformat()
        return {k: int(self.db.get_setting(f'community_{k}_count:{day}', 0) or 0) for k in ('search', 'fetch')}

    def _json(self, request: Request, timeout=None) -> dict:
        with self.open(request, timeout=timeout or self.timeout) as response:
            raw = response.read(MAX_RESPONSE+1)
        if len(raw) > MAX_RESPONSE: raise SearchError('响应太大')
        return json.loads(raw)

    def search(self, query: str, count=5) -> list[Hit]:
        """搜一次。关掉 / 超额返回空列表（不报错，学习就只靠群里的用法）。"""
        query = re.sub(r'\s+', ' ', str(query or '')).strip()[:120]
        if not query or not self.enabled or not self._budget('search'): return []
        try:
            return self._deepseek(query, count) if self.backend() == 'deepseek' else self._wikipedia(query, count)
        except Exception:
            return []   # 上游错误可能带请求摘要或密钥信息，不记录、不外传

    def fetch(self, url: str) -> str:
        if not re.match(r'https://', str(url or '')) or not self.enabled or self.backend() != 'deepseek' or not self._budget('fetch'): return ''
        try:
            data = self._json(self._post('fetch', {'url': url[:2000]}))
            return str(data.get('content') or '')[:6000]
        except Exception:
            return ''

    def _post(self, action: str, body: dict) -> Request:
        base = str(self.client.base_url).rstrip('/')
        if not base.endswith('/v1'): base += '/v1'
        return Request(f'{base}/tools/{action}', data=json.dumps(body, ensure_ascii=False).encode(), method='POST',
                       headers={'Authorization': f'Bearer {self.client.api_key}', 'Content-Type': 'application/json'})

    def _deepseek(self, query: str, count: int) -> list[Hit]:
        data = self._json(self._post('search', {'query': query, 'count': max(1, min(10, count)), 'need_content': False}))
        hits = []
        for item in (data.get('results') or [])[:count]:
            if not isinstance(item, dict) or not re.match(r'https?://', str(item.get('url') or '')): continue
            hits.append(Hit(str(item.get('title') or '')[:80], str(item['url'])[:500],
                            str(item.get('summary') or item.get('snippet') or '')[:900], str(item.get('site_name') or '')[:30]))
        return hits

    def _wikipedia(self, query: str, count: int) -> list[Hit]:
        term = re.sub(r'\s*(梗|意思|是什么|网络用语|黑话)+\s*', ' ', query).strip() or query
        found = self._json(Request(f'{WIKI}?action=query&list=search&format=json&srlimit={min(3, count)}&srsearch={quote(term)}',
                                   headers={'User-Agent': 'AICompanion/0.2 (desktop companion; community glossary)'}))
        titles = [r.get('title') for r in (found.get('query', {}).get('search') or []) if isinstance(r, dict) and r.get('title')][:min(3, count)]
        if not titles: return []
        pages = self._json(Request(f'{WIKI}?action=query&prop=extracts&exintro=1&explaintext=1&format=json&titles={quote("|".join(titles))}',
                                   headers={'User-Agent': 'AICompanion/0.2 (desktop companion; community glossary)'}))
        hits = []
        for page in (pages.get('query', {}).get('pages') or {}).values():
            title = str(page.get('title') or ''); text = str(page.get('extract') or '')
            if title and text:
                hits.append(Hit(title[:80], 'https://zh.wikipedia.org/wiki/'+quote(title.replace(' ', '_')), text[:900], '维基百科'))
        return hits
