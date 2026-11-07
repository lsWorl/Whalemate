"""聊天平台桥接（T9）：身份决定能用的上下文；频道隔离；本机接口的令牌。不依赖 Qt、网络和真实模型。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
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
        self.db.upsert_memory('用户的秘密爱好是集邮', self.cid, 'hobby', scope='pet')
        self.db.upsert_memory('她的公开爱好是看海', self.cid, 'public-hobby', scope='public')
        result = self.ask(STRANGER, '你好，我叫大壮', group=GROUP, name='大壮')
        self.assertEqual(result['reply'], '好呀')
        history, prompt = self.client.calls[-1]
        text = prompt+' '.join(t.content for t in history)
        for private in ('桌面上说的悄悄话', '之前聊过搬家的事', '集邮', '信任程度'):
            self.assertNotIn(private, text)
        self.assertIn('看海', prompt); self.assertIn('公开场合', prompt); self.assertIn('群聊', prompt)
        self.assertIn('大壮：你好', history[-1].content)            # 群里带上说话人
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
            self.assertEqual([(r['content'], r['channel']) for r in rows], [('旧消息', 'desktop')])
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
