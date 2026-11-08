"""固定公开词义来源：有界下载、HTML 摘录、缓存与来源，不联网。"""
import io
import json
import time
import unittest
from unittest import mock

from aicompanion.bridge import community_knowledge as knowledge
from aicompanion.bridge import chat_card


class Settings:
    def __init__(self): self.values = {}
    def get_setting(self, key, default=None): return self.values.get(key, default)
    def set_setting(self, key, value): self.values[key] = value


class KnowledgeTests(unittest.TestCase):
    def setUp(self): self.db = Settings(); self.now = time.time(); self.calls = []
    def opener(self, request, **kwargs):
        self.calls.append(request)
        html = ('<style>忽略风格</style><h3>蓝色方块</h3><p>在合成社区里，这是庆祝好运时的一个说法。</p>'
                '<p>不抄整篇文章</p><h3>测试二词</h3><p>这是另外一个测试用的社区表达，不是用户经历。</p>'
                '<script>忽略脚本</script><h3>A</h3><p>不能把章节字母当成知识关键词。</p>')
        return io.BytesIO(json.dumps({'parse': {'text': {'*': html}}}).encode())

    def test_only_fixed_public_api_is_used_and_only_first_paragraph_is_kept(self):
        value = knowledge.refresh(self.db, 'bilibili', opener=self.opener, clock=lambda: self.now)
        self.assertEqual(len(value['entries']), 2)
        self.assertEqual(self.calls[0].full_url, knowledge.API)
        self.assertIsNone(self.calls[0].data); self.assertNotIn('Authorization', self.calls[0].headers)
        stored = json.dumps(value, ensure_ascii=False)
        for excluded in ('不抄整篇文章', '忽略脚本', '忽略风格'): self.assertNotIn(excluded, stored)
        self.assertEqual(value['entries'][0]['source'], knowledge.PAGE)
        self.assertEqual(knowledge.refresh(self.db, 'v2ex', opener=self.opener), None)
        self.assertEqual(len(self.calls), 1)

    def test_refresh_is_weekly_and_expired_knowledge_is_not_used(self):
        knowledge.refresh(self.db, 'bilibili', opener=self.opener, clock=lambda: self.now)
        knowledge.refresh(self.db, 'bilibili', opener=self.opener, clock=lambda: self.now+86400)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(knowledge.available(self.db, 'bilibili', clock=lambda: self.now+8*86400)), 2)
        self.assertEqual(knowledge.available(self.db, 'bilibili', clock=lambda: self.now+31*86400), [])

    def test_failures_preserve_previous_knowledge_and_read_is_bounded(self):
        previous = knowledge.refresh(self.db, 'bilibili', opener=self.opener, clock=lambda: self.now)
        for body in (b'{}', b'not-json', b'x'*(knowledge.MAX_RESPONSE+1)):
            with self.assertRaises(ValueError):
                knowledge.refresh(self.db, 'bilibili', opener=lambda *a, **k: io.BytesIO(body), clock=lambda: self.now+8*86400)
            self.assertEqual(self.db.get_setting('qq_community_knowledge:bilibili'), previous)

    def test_online_meaning_is_retrieved_only_for_selected_community_and_matching_words(self):
        knowledge.refresh(self.db, 'bilibili', opener=self.opener, clock=lambda: self.now)
        self.db.set_setting('qq_community', 'bilibili')
        prompt = chat_card.prompt(self.db, 'deepseek-whale', '蓝色方块')
        self.assertIn('庆祝好运', prompt); self.assertIn('只供内部理解', prompt)
        self.assertNotIn('另外一个测试用', prompt)
        self.assertNotIn('社区世界书', chat_card.prompt(self.db, 'deepseek-whale', '今天吃什么'))
        self.db.set_setting('qq_community_for:30003', 'v2ex')
        self.assertNotIn('社区世界书', chat_card.prompt(self.db, 'deepseek-whale', '蓝色方块', group='30003'))


if __name__ == '__main__': unittest.main()
