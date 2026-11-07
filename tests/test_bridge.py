"""聊天平台桥接（T9）：身份决定能用的上下文；频道隔离；本机接口的令牌。不依赖 Qt、网络和真实模型。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from aicompanion.bridge.server import BridgeServer
from aicompanion.bridge.service import BridgeService
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database

OWNER, STRANGER, GROUP = '10001', '20002', '30003'


class RecordingClient:
    """记下每次请求的提示和历史，回一句固定的话（带一个台词标注，检查会被去掉）。"""
    model = 'mock'

    def __init__(self): self.calls = []

    def generate_reply_stream(self, history, prompt, cancellation=None):
        self.calls.append((history, prompt))
        yield '好呀【开心】'


class Quota:
    def __init__(self): self.state = 'ok'
    def check_silent(self, purpose): return self.state


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'pet.sqlite3'); self.db.initialize()
        self.client = RecordingClient()
        self.controller = ChatController(self.db, MemoryService(self.db), self.client)
        from aicompanion.pet.script import Vocab
        self.controller.vocabs[self.controller.character.id] = Vocab({'开心': 'happy'}, {})
        self.quota = Quota()
        self.service = BridgeService(self.db, self.controller, quota=self.quota)
        self.db.set_setting('bridge_owner:qq', OWNER); self.db.set_setting('bridge_groups', [GROUP])
        self.cid = self.controller.character.id

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, sender, text, group='', name='小明'):
        return self.service.handle({'platform': 'qq', 'sender_id': sender, 'sender_name': name, 'group_id': group, 'text': text})

    def test_owner_private_chat_is_the_same_relationship_as_desktop(self):
        self.db.add_message('user', '桌面上说的悄悄话', self.cid)
        self.db.save_summary(self.cid, '之前聊过搬家的事', 1)
        result = self.ask(OWNER, '我叫阿青')
        self.assertEqual(result, {'reply': '好呀', 'silent': False, 'reason': 'ok'})   # 台词标注去掉
        history, prompt = self.client.calls[-1]
        self.assertIn('桌面上说的悄悄话', [t.content for t in history][0])
        self.assertIn('之前聊过搬家的事', prompt); self.assertIn('信任程度', prompt); self.assertIn('QQ私聊', prompt)
        rows = self.db.recent_messages(10, self.cid)                # desktop 频道
        self.assertEqual([(r['role'], r['source']) for r in rows][-2:], [('user', 'qq'), ('assistant', 'qq')])
        self.assertTrue(any('阿青' in m['content'] for m in self.db.recent_memories(character_id=self.cid)))   # 主人说的会记住

    def test_group_is_public_and_isolated(self):
        self.db.add_message('user', '桌面上说的悄悄话', self.cid)
        self.db.save_summary(self.cid, '之前聊过搬家的事', 1)
        self.db.upsert_memory('用户的秘密爱好是集邮', self.cid, 'hobby', scope='private')   # 标成"只在私下用"
        self.db.upsert_memory('主人喜欢看海', self.cid, 'sea')                                  # 默认共享
        result = self.ask(STRANGER, '你好，我叫大壮', group=GROUP, name='大壮')
        self.assertEqual(result['reply'], '好呀')
        history, prompt = self.client.calls[-1]
        text = prompt+' '.join(t.content for t in history)
        for private in ('桌面上说的悄悄话', '之前聊过搬家的事', '集邮', '信任程度'):
            self.assertNotIn(private, text)
        self.assertIn('看海', prompt); self.assertIn('公开场合', prompt); self.assertIn('群聊', prompt)   # 共享记忆在群里也能用
        self.assertIn('大壮#0002：你好', history[-1].content)       # 群里带上说话人（昵称 + 号码后四位）
        self.assertEqual(len(self.db.recent_messages(10, self.cid)), 1)   # 桌面频道没有多出群聊内容
        self.assertEqual(len(self.db.recent_messages(10, self.cid, channel=f'qq:group:{GROUP}')), 2)
        self.assertFalse(any('大壮' in m['content'] for m in self.db.recent_memories(character_id=self.cid)))   # 不提取记忆

    def test_owner_in_group_is_still_public(self):
        self.db.add_message('user', '桌面上说的悄悄话', self.cid)
        self.ask(OWNER, '大家好', group=GROUP)
        history, prompt = self.client.calls[-1]
        self.assertNotIn('桌面上说的悄悄话', prompt+' '.join(t.content for t in history))
        self.assertIn('你的主人', prompt)

    def test_who_gets_an_answer(self):
        self.assertEqual(self.ask(STRANGER, '在吗')['reason'], 'not_allowed')                 # 陌生人私聊默认不回
        self.assertEqual(self.ask(STRANGER, '在吗', group='99999')['reason'], 'not_allowed')  # 没允许的群
        self.db.set_setting('bridge_strangers', True)
        self.assertEqual(self.ask(STRANGER, '在吗')['reason'], 'ok')
        self.assertEqual(len(self.db.recent_messages(10, self.cid, channel=f'qq:user:{STRANGER}')), 2)
        self.assertEqual(self.ask(OWNER, '   ')['reason'], 'empty')
        for bad in ({'platform': 'QQ!', 'sender_id': OWNER, 'text': 'x'}, {'platform': 'qq', 'sender_id': '../x y', 'text': 'x'}):
            self.assertEqual(self.service.handle(bad)['reason'], 'bad_request')

    def test_limits(self):
        self.db.set_setting('bridge_public_daily', 1)
        self.assertEqual(self.ask(STRANGER, '一', group=GROUP)['reason'], 'ok')
        self.assertEqual(self.ask(STRANGER, '二', group=GROUP)['reason'], 'daily_limit')
        self.assertEqual(self.ask(OWNER, '主人不受群的条数限制')['reason'], 'ok')
        self.quota.state = 'stop'
        self.assertEqual(self.ask(OWNER, '额度用完')['reason'], 'quota'); self.assertFalse(self.ask(OWNER, '再说')['silent'])
        self.assertTrue(self.ask(STRANGER, '额度用完', group=GROUP)['silent'])

    def test_no_task_offers_on_chat_platforms(self):
        self.controller.agent_enabled = True
        self.ask(OWNER, '帮我整理一下文件')
        self.assertNotIn('【动手】', self.client.calls[-1][1])

    def test_model_error_is_silent(self):
        def boom(*a, **k):
            raise RuntimeError('网络断了'); yield
        self.client.generate_reply_stream = boom
        result = self.ask(OWNER, '在吗')
        self.assertEqual((result['silent'], result['reason']), (True, 'error'))


class MemorySharingTests(BridgeTests.__base__):
    """群里听来的事整理进同一份记忆（写清楚是谁的），桌面上也能用；主人私下的对话摘要不进群。"""
    setUp = BridgeTests.setUp; tearDown = BridgeTests.tearDown; ask = BridgeTests.ask

    def test_group_talk_becomes_shared_memory(self):
        channel = f'qq:group:{GROUP}'
        self.db.set_setting('bridge_memory_every', 0)                       # 先关掉自动整理，下面同步跑一次
        self.ask('11110001', '我是程序员，最近在学 Rust', group=GROUP, name='大壮')
        self.ask('22220002', '大壮好厉害', group=GROUP, name='小美')
        memory_service = self.controller.memory_service
        self.assertEqual(memory_service.pending(self.cid, channel), 2)
        source = [r['id'] for r in self.db.recent_messages(10, self.cid, channel=channel) if r['role'] == 'user']
        analysis = {'summary': '不应保存', 'memories': [{'content': '群友大壮#0001 是程序员，在学 Rust', 'fact_key': 'qq#0001:job',
                     'category': 'identity', 'importance': 2, 'confidence': 0.9, 'source_ids': source[:1]}]}
        seen = []
        class Analyzer:
            def generate_reply_stream(self_, history, prompt, cancellation=None):
                seen.append(prompt); yield json.dumps(analysis, ensure_ascii=False)
        self.controller.llm_client = Analyzer()
        self.assertFalse(self.service.maybe_remember(self.cid, channel, background=False))   # 关着时不整理
        self.db.set_setting('bridge_memory_every', 2)
        self.assertTrue(self.service.maybe_remember(self.cid, channel, background=False))
        memory = next((m for m in self.db.recent_memories(character_id=self.cid) if m['fact_key'] == 'qq#0001:job'), None)
        self.assertIsNotNone(memory); self.assertEqual(memory['scope'], 'qq')
        self.assertIn('不要把群友的事记成主人的', seen[-1])
        self.assertEqual(self.db.load_summary(self.cid), '')                # 群聊不写对话摘要
        self.assertEqual(memory_service.pending(self.cid, channel), 0)
        history, prompt = self.controller.context.build(self.controller.character, '大壮')   # 桌面上她也知道
        self.assertIn('大壮#0001 是程序员', prompt)

    def test_owner_label_in_group_and_no_regex_memory_for_group(self):
        self.ask(OWNER, '我叫阿青', group=GROUP, name='阿青')
        self.assertIn('主人阿青#0001：我叫阿青', self.client.calls[-1][0][-1].content)
        self.assertFalse(any('名字是阿青' in m['content'] for m in self.db.recent_memories(character_id=self.cid)))   # 群里不按"用户"套规则

    def test_private_summary_and_marked_memories_stay_out_of_groups(self):
        self.db.save_summary(self.cid, '主人最近在准备考试', 1)
        self.db.upsert_memory('主人的病情', self.cid, 'health')
        mid = next(m['id'] for m in self.db.recent_memories(character_id=self.cid) if m['fact_key'] == 'health')
        self.db.set_memory_scope([mid], self.cid, 'private')
        self.ask(STRANGER, '她最近怎么样', group=GROUP)
        prompt = self.client.calls[-1][1]
        self.assertNotIn('准备考试', prompt); self.assertNotIn('病情', prompt)
        self.ask(OWNER, '在吗')
        self.assertIn('病情', self.client.calls[-1][1]); self.assertIn('准备考试', self.client.calls[-1][1])


class ConcurrencyTests(BridgeTests.__base__):
    """Codex 审计复现的并发问题：用线程屏障安排合法的并发时序，断言模型调用次数和每一轮看到的历史。"""
    setUp = BridgeTests.setUp; tearDown = BridgeTests.tearDown; ask = BridgeTests.ask

    def run_parallel(self, *calls):
        results = [None]*len(calls); barrier = threading.Barrier(len(calls))
        def worker(i, fn):
            barrier.wait(); results[i] = fn()
        threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(calls)]
        for t in threads: t.start()
        for t in threads: t.join(10)
        return results

    def test_daily_limit_holds_under_concurrency(self):
        self.db.set_setting('bridge_public_daily', 1)
        results = self.run_parallel(lambda: self.ask(STRANGER, '一', group=GROUP), lambda: self.ask(STRANGER, '二', group=GROUP))
        self.assertEqual(sorted(r['reason'] for r in results), ['daily_limit', 'ok'])
        self.assertEqual(len(self.client.calls), 1)                        # 最多启动一次模型
        from datetime import date
        self.assertEqual(self.db.get_setting(f'bridge_public_count:{date.today().isoformat()}'), 1)

    def test_reserve_daily_is_atomic_across_service_instances(self):
        from aicompanion.bridge.service import BridgeService
        other = BridgeService(self.db, self.controller, quota=self.quota)   # 设置保存后重建的新实例
        self.db.set_setting('bridge_public_daily', 1)
        results = self.run_parallel(lambda: self.ask(STRANGER, '一', group=GROUP),
                                    lambda: other.handle({'platform': 'qq', 'sender_id': STRANGER, 'group_id': GROUP, 'text': '二'}))
        self.assertEqual(sorted(r['reason'] for r in results), ['daily_limit', 'ok']); self.assertEqual(len(self.client.calls), 1)
        hits = self.run_parallel(*[lambda i=i: self.db.reserve_daily('probe', 3) for i in range(8)])
        self.assertEqual(hits.count(True), 3)

    def test_quota_exhausted_while_queued_stops_next_request(self):
        original = self.client.generate_reply_stream
        def first_then_stop(history, prompt, cancellation=None):
            self.quota.state = 'stop'                                   # 第一条生成期间额度用完
            time.sleep(0.2)
            yield from original(history, prompt, cancellation)
        self.client.generate_reply_stream = first_then_stop
        results = self.run_parallel(lambda: self.ask(STRANGER, '一', group=GROUP), lambda: self.ask(STRANGER, '二', group=GROUP))
        self.assertEqual(sorted(r['reason'] for r in results), ['ok', 'quota'])
        self.assertEqual(len(self.client.calls), 1)                        # 排队的那条没有再发给模型

    def test_concurrent_owner_messages_each_answer_their_own_turn(self):
        """第一条生成时第二条已经到达：第一轮只看到"第一条"，第二轮以"第二条"结尾、前面是第一轮的回复。"""
        second_arrived = threading.Event(); original = self.client.generate_reply_stream
        def slow(history, prompt, cancellation=None):
            if len(self.client.calls) == 0: second_arrived.wait(2); time.sleep(0.1)
            yield from original(history, prompt, cancellation)
        self.client.generate_reply_stream = slow
        def first(): return self.ask(OWNER, '第一条')
        def second():
            time.sleep(0.05); second_arrived.set(); return self.ask(OWNER, '第二条')
        results = self.run_parallel(first, second)
        self.assertEqual([r['reason'] for r in results], ['ok', 'ok'])
        (h1, _), (h2, _) = self.client.calls
        self.assertEqual((h1[-1].role, h1[-1].content), ('user', '第一条'))
        self.assertEqual([(t.role, t.content) for t in h2[-3:]], [('user', '第一条'), ('assistant', '好呀'), ('user', '第二条')])

    def test_same_nickname_in_group_is_told_apart(self):
        self.ask('11110001', '我是一号', group=GROUP, name='小明')
        self.ask('22220002', '我是二号', group=GROUP, name='小明')
        history = self.client.calls[-1][0]
        text = ' '.join(t.content for t in history)
        self.assertIn('小明#0001：我是一号', text); self.assertIn('小明#0002：我是二号', text)
        rows = self.db.recent_messages(10, self.cid, channel=f'qq:group:{GROUP}')
        self.assertEqual([r['sender'] for r in rows if r['role'] == 'user'], ['qq:11110001', 'qq:22220002'])
        self.assertNotIn('11110001', ' '.join(t.content for t in history))   # 完整号码不交给模型


class MigrationTests(unittest.TestCase):
    def test_v3_database_gets_desktop_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'old.sqlite3'
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("""CREATE TABLE characters (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    speaking_style TEXT NOT NULL, model_path TEXT NOT NULL DEFAULT '', voice TEXT NOT NULL DEFAULT '');
                    CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, character_id TEXT NOT NULL DEFAULT 'emilia',
                    turn_id TEXT, status TEXT NOT NULL DEFAULT 'complete', source TEXT NOT NULL DEFAULT 'text');
                    CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    character_id TEXT NOT NULL DEFAULT 'emilia', category TEXT NOT NULL DEFAULT 'legacy', fact_key TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1, source_ids TEXT NOT NULL DEFAULT '[]', clarity REAL NOT NULL DEFAULT 1,
                    recalled_at TEXT, scope TEXT NOT NULL DEFAULT 'pet');
                    INSERT INTO chat_messages(role,content,character_id) VALUES ('user','旧消息','emilia');
                    PRAGMA user_version=3;""")
            db = Database(path); db.initialize()
            rows = db.recent_messages(5, 'emilia')
            self.assertEqual([(r['content'], r['channel'], r['sender']) for r in rows], [('旧消息', 'desktop', '')])
            self.assertTrue(list(Path(tmp).glob('old.backup-*.sqlite3')))   # 升级前自动备份


class ServerTests(unittest.TestCase):
    def test_token_and_round_trip(self):
        class Service:
            controller = type('C', (), {'character': DEFAULT_CHARACTER})()
            def handle(self, payload): return {'reply': 'echo:'+payload['text'], 'silent': False, 'reason': 'ok'}
        server = BridgeServer(Service(), 0, 'secret-token')
        base = f'http://127.0.0.1:{server.port}'
        def call(path, body=None, token='secret-token'):
            data = json.dumps(body).encode() if body is not None else None
            request = urllib.request.Request(base+path, data=data, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5) as response: return response.status, json.loads(response.read())
        try:
            self.assertEqual(call('/v1/chat', {'text': '你好'}), (200, {'reply': 'echo:你好', 'silent': False, 'reason': 'ok'}))
            self.assertEqual(call('/v1/ping')[1]['ok'], True)
            for path, body, token in (('/v1/chat', {'text': 'x'}, 'wrong'), ('/v1/ping', None, '')):
                with self.assertRaises(urllib.error.HTTPError) as caught: call(path, body, token)
                self.assertEqual(caught.exception.code, 401)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(base+'/v1/chat', data=b'not json', headers={'Authorization': 'Bearer secret-token'}), timeout=5)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(server.httpd.server_address[0], '127.0.0.1')
        finally:
            server.close()


if __name__ == '__main__':
    unittest.main()
