"""固定社区百科的有界摘录：保存词义及来源，热榜标题不冒充知识。"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler

PAGE = 'https://wiki.biligame.com/ys/黑话'
API = ('https://wiki.biligame.com/ys/api.php?action=parse&format=json&page=%E9%BB%91%E8%AF%9D'
       '&prop=text&disablelimitreport=1&disableeditsection=1')
MAX_RESPONSE = 1024*1024


class Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading = ''; self.title = ''; self.paragraph = False; self.skip = 0
        self.title_parts = []; self.parts = []; self.entries = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'table'): self.skip += 1
        if self.skip: return
        if re.fullmatch(r'h[2-6]', tag):
            self.heading = tag; self.title_parts = []; self.title = ''; self.parts = []; self.paragraph = False
        elif tag == 'p' and self.title and not self.parts: self.paragraph = True

    def handle_data(self, data):
        if self.skip: return
        if self.heading: self.title_parts.append(data)
        elif self.paragraph and sum(map(len, self.parts)) < 500: self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'table'):
            self.skip = max(0, self.skip-1); return
        if self.skip: return
        if tag == self.heading:
            self.title = re.sub(r'\s+', ' ', ''.join(self.title_parts)).strip()[:40]; self.heading = ''
        elif tag == 'p' and self.paragraph:
            self.paragraph = False
            content = re.sub(r'\s+', ' ', ''.join(self.parts)).strip()[:300]
            # 一字词 / 章节字母容易误命中，不据它们触发背景；只摘首段，不存整页。
            if 2 <= len(self.title) <= 24 and len(content) >= 12:
                entry = {'keys': [self.title], 'content': content, 'source': PAGE}
                if self.title in ('猫猫', '水位', '女逝'):
                    entry.update(selective=True, secondary_keys=['原神', '提瓦特', '卡池', '祈愿', '深渊', '圣遗物'])
                self.entries.append(entry)
            self.title = ''; self.parts = []


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def refresh(database, source, opener=None, clock=time.time):
    if source != 'bilibili': return None  # 没有可靠释义源的社区不拿热榜编词义
    previous = database.get_setting('qq_community_knowledge:'+source, {}) or {}
    if previous.get('entries') and clock()-previous.get('at', 0) < 7*86400: return previous
    request = Request(API, headers={'User-Agent': 'AICompanion/0.1 (+public-community)'})
    with (opener or build_opener(NoRedirect()).open)(request, timeout=15) as response:
        raw = response.read(MAX_RESPONSE+1)
    if len(raw) > MAX_RESPONSE: raise ValueError('社区知识响应过大，旧知识保留')
    data = json.loads(raw)
    html = data.get('parse', {}).get('text', {}).get('*', '') if isinstance(data, dict) else ''
    if not isinstance(html, str) or not html: raise ValueError('没有读到社区释义，旧知识保留')
    parser = Extractor(); parser.feed(html)
    from aicompanion.bridge.chat_card import normalize_book
    # 50 条上限与手动世界书一致；重复标题只留首条。
    unique = {}
    for entry in parser.entries: unique.setdefault(entry['keys'][0], entry)
    entries = normalize_book({'entries': list(unique.values())[:50]})
    if not entries: raise ValueError('没有可用的关键词释义，旧知识保留')
    value = {'at': clock(), 'source': '哔哩哔哩 BWIKI / 原神社区黑话', 'url': PAGE, 'entries': entries}
    database.set_setting('qq_community_knowledge:'+source, value)
    return value


def available(database, source, clock=time.time):
    if source != 'bilibili': return []
    value = database.get_setting('qq_community_knowledge:'+source, {}) or {}
    if clock()-value.get('at', 0) > 30*86400: return []
    from aicompanion.bridge.chat_card import normalize_book
    try: return normalize_book({'entries': value.get('entries', [])})
    except ValueError: return []
