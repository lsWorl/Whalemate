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
        self.assertIn('大壮#1：你好', history[-1].content)          # 群里带上说话人（昵称 + 不重复的编号）
        self.assertEqual(len(self.db.recent_messages(10, self.cid)), 1)   # 桌面频道没有多出群聊内容
        self.assertEqual(len(self.db.recent_messages(10, self.cid, channel=f'qq:group:{GROUP}')), 2)
        self.assertFalse(any('大壮' in m['content'] for m in self.db.recent_memories(character_id=self.cid)))   # 不提取记忆

    def test_owner_in_group_is_still_public(self):
        self.db.add_message('user', '桌面上说的悄悄话', self.cid)
        self.ask(OWNER, '大家好', group=GROUP)
        history, prompt = self.client.calls[-1]
        self.assertNotIn('桌面上说的悄悄话', prompt+' '.join(t.content for t in history))
        self.assertIn('你爹', prompt); self.assertNotIn('主人', prompt)

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
        analysis = {'summary': '不应保存', 'memories': [{'content': '群友大壮#1 是程序员，在学 Rust', 'fact_key': 'qq#1:job',
                     'category': 'identity', 'importance': 2, 'confidence': 0.9, 'source_ids': source[:1]}]}
        seen = []
        class Analyzer:
            def generate_reply_stream(self_, history, prompt, cancellation=None):
                seen.append(prompt); yield json.dumps(analysis, ensure_ascii=False)
        self.controller.llm_client = Analyzer()
        self.assertFalse(self.service.maybe_remember(self.cid, channel, background=False))   # 关着时不整理
        self.db.set_setting('bridge_memory_every', 2)
        self.assertTrue(self.service.maybe_remember(self.cid, channel, background=False))
        memory = next((m for m in self.db.recent_memories(character_id=self.cid) if m['fact_key'] == 'qq#1:job'), None)
        self.assertIsNotNone(memory); self.assertEqual(memory['scope'], 'qq')
        self.assertIn('不要把群友的事记成爹的', seen[-1])
        self.assertEqual(self.db.load_summary(self.cid), '')                # 群聊不写对话摘要
        self.assertEqual(memory_service.pending(self.cid, channel), 0)
        history, prompt = self.controller.context.build(self.controller.character, '大壮')   # 桌面上她也知道
        self.assertIn('大壮#1 是程序员', prompt)

    def test_private_memory_cannot_leak_through_group_memory_analysis(self):
        """Codex 复现：private 记忆进入公开整理输入，被换个键复制成共享。整理输入不含 private；复制和覆盖都被拒。"""
        channel = f'qq:group:{GROUP}'
        self.db.upsert_memory('主人的编号是 SYNTHETIC-PRIVATE-4827', self.cid, 'secret-code', scope='private')
        self.db.upsert_memory('主人喜欢蓝色', self.cid, 'color')                 # 桌面记住的共享事实（scope=pet）
        self.db.set_setting('bridge_memory_every', 0)
        self.ask(STRANGER, '今天聊些什么', group=GROUP)
        source = [r['id'] for r in self.db.recent_messages(10, self.cid, channel=channel) if r['role'] == 'user']
        crafted = {'summary': '', 'memories': [
            {'content': '群里提到 SYNTHETIC-PRIVATE-4827', 'fact_key': 'qq#2:copied-test', 'category': 'event', 'importance': 2, 'confidence': 0.9, 'source_ids': source},
            {'content': '主人喜欢红色', 'fact_key': 'color', 'category': 'preference', 'importance': 2, 'confidence': 0.9, 'source_ids': source},
            {'content': '群友今天想聊天', 'fact_key': 'qq#2:mood', 'category': 'event', 'importance': 1, 'confidence': 0.9, 'source_ids': source}]}
        inputs = []
        class Analyzer:
            def generate_reply_stream(self_, history, prompt, cancellation=None):
                inputs.append(history[0].content); yield json.dumps(crafted, ensure_ascii=False)
        from aicompanion.domain.cancellation import CancellationToken
        self.controller.memory_service.analyze(self.cid, Analyzer(), CancellationToken(), channel=channel)
        self.assertNotIn('SYNTHETIC-PRIVATE-4827', inputs[0]); self.assertNotIn('secret-code', inputs[0])   # 整理输入不含 private
        keys = {m['fact_key']: m for m in self.db.recent_memories(character_id=self.cid)}
        self.assertNotIn('qq#2:copied-test', keys)                               # 复制被拒
        self.assertEqual(keys['color']['content'], '主人喜欢蓝色')                # 群里不能改桌面记住的事实
        self.assertEqual(keys['qq#2:mood']['scope'], 'qq')                       # 正常的群记忆照常记
        self.ask(STRANGER, '她有什么编号', group=GROUP)
        self.assertNotIn('SYNTHETIC-PRIVATE-4827', self.client.calls[-1][1])    # 之后的群回复也看不到

    def test_marked_private_while_analyzing_is_not_copied(self):
        """Codex 复现的竞态：共享事实进入整理输入 → 整理器挂起 → 用户标成私下 → 整理器返回换键副本。副本不能写成共享。"""
        channel = f'qq:group:{GROUP}'
        self.db.upsert_memory('主人的编号是 SYNTHETIC-PRIVATE-4827', self.cid, 'code')   # 起初是共享的
        self.db.set_setting('bridge_memory_every', 0)
        self.ask(STRANGER, '今天聊些什么', group=GROUP)
        source = [r['id'] for r in self.db.recent_messages(10, self.cid, channel=channel) if r['role'] == 'user']
        result = {'summary': '', 'memories': [
            {'content': '群里提到 SYNTHETIC-PRIVATE-4827', 'fact_key': 'qq#2:copied-test', 'category': 'event', 'importance': 2, 'confidence': 0.9, 'source_ids': source},
            {'content': '群友今天想聊天', 'fact_key': 'qq#2:mood', 'category': 'event', 'importance': 1, 'confidence': 0.9, 'source_ids': source}]}
        started, release, inputs = threading.Event(), threading.Event(), []
        class SlowAnalyzer:
            def generate_reply_stream(self_, history, prompt, cancellation=None):
                inputs.append(history[0].content); started.set(); release.wait(5)
                yield json.dumps(result, ensure_ascii=False)
        from aicompanion.domain.cancellation import CancellationToken
        worker = threading.Thread(target=lambda: self.controller.memory_service.analyze(self.cid, SlowAnalyzer(), CancellationToken(), channel=channel))
        worker.start(); self.assertTrue(started.wait(5))
        self.assertIn('SYNTHETIC-PRIVATE-4827', inputs[0])                       # 开始时是共享的，合法进入了输入
        mid = next(m['id'] for m in self.db.recent_memories(character_id=self.cid) if m['fact_key'] == 'code')
        self.db.set_memory_scope([mid], self.cid, 'private')                     # 整理期间用户收回
        release.set(); worker.join(5)
        keys = {m['fact_key']: m for m in self.db.recent_memories(character_id=self.cid)}
        self.assertNotIn('qq#2:copied-test', keys); self.assertEqual(keys['code']['scope'], 'private')
        self.assertEqual(keys['qq#2:mood']['scope'], 'qq')                       # 普通的群记忆照常写
        self.ask(STRANGER, '她有什么编号', group=GROUP)
        self.assertNotIn('SYNTHETIC-PRIVATE-4827', self.client.calls[-1][1])

    def test_guard_and_scope_change_do_not_interleave(self):
        """守卫在写事务里读最新的 private：检查与写入之间不能插进 set_memory_scope。"""
        from aicompanion.services.memory_service import MemoryService
        self.db.upsert_memory('主人的编号是 SYNTHETIC-PRIVATE-4827', self.cid, 'code')
        mid = next(m['id'] for m in self.db.recent_memories(character_id=self.cid) if m['fact_key'] == 'code')
        inner = MemoryService.public_guard('群里提到 SYNTHETIC-PRIVATE-4827', self.cid)
        changed = []
        def guard(connection, key):
            ok = inner(connection, key)                                          # 此刻还是共享 → 允许
            t = threading.Thread(target=lambda: (self.db.set_memory_scope([mid], self.cid, 'private'), changed.append(True)))
            t.start(); t.join(0.5)
            self.assertEqual(changed, [])                                        # 设置页的写入被事务挡住，等这次写完
            guard.thread = t; return ok
        self.assertTrue(self.db.upsert_memory('群里提到 SYNTHETIC-PRIVATE-4827', self.cid, 'qq#2:copy', scope='qq', guard=guard))
        guard.thread.join(10); self.assertEqual(changed, [True])

    def test_marking_private_also_covers_existing_copies(self):
        """副本在标记之前已经从群里整理出来：标成私下时一起收回，之后群里看不到。"""
        self.db.upsert_memory('主人的编号是 SYNTHETIC-PRIVATE-4827', self.cid, 'code')
        self.db.upsert_memory('群里提到主人的编号 SYNTHETIC-PRIVATE-4827', self.cid, 'qq#2:copy', scope='qq')
        self.db.upsert_memory('群友大壮#1 是程序员', self.cid, 'qq#1:job', scope='qq')
        mid = next(m['id'] for m in self.db.recent_memories(character_id=self.cid) if m['fact_key'] == 'code')
        self.controller.memory_service.set_scope([mid], self.cid, 'private')
        scopes = {m['fact_key']: m['scope'] for m in self.db.recent_memories(character_id=self.cid)}
        self.assertEqual(scopes, {'code': 'private', 'qq#2:copy': 'private', 'qq#1:job': 'qq'})
        self.ask(STRANGER, '她有什么编号', group=GROUP)
        self.assertNotIn('SYNTHETIC-PRIVATE-4827', self.client.calls[-1][1]); self.assertIn('程序员', self.client.calls[-1][1])

    def test_owner_label_in_group_and_no_regex_memory_for_group(self):
        self.ask(OWNER, '我叫阿青', group=GROUP, name='阿青')
        self.assertIn('【爹】阿青#1：我叫阿青', self.client.calls[-1][0][-1].content)
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

    def test_same_nickname_and_same_last_digits_are_told_apart(self):
        """Codex：后四位并不唯一。编号按账号全局分配、永不重复，完整号码不交给模型。"""
        self.ask('11110001', '我是一号', group=GROUP, name='小明')
        self.ask('22220001', '我是二号', group=GROUP, name='小明')     # 同昵称、同后四位
        self.ask('11110001', '还是一号', group=GROUP, name='小明')
        text = ' '.join(t.content for t in self.client.calls[-1][0])
        self.assertIn('小明#1：我是一号', text); self.assertIn('小明#2：我是二号', text); self.assertIn('小明#1：还是一号', text)
        rows = self.db.recent_messages(10, self.cid, channel=f'qq:group:{GROUP}')
        self.assertEqual([r['sender'] for r in rows if r['role'] == 'user'], ['qq:11110001', 'qq:22220001', 'qq:11110001'])
        self.assertNotIn('11110001', text); self.assertNotIn('0001', text)

    def queued(self, payload_fn, change):
        """先占住处理锁让请求排队，排队期间改设置，再放行。返回结果。"""
        from aicompanion.bridge.service import PROCESS_LOCK
        result = {}
        PROCESS_LOCK.acquire()
        try:
            t = threading.Thread(target=lambda: result.update(payload_fn())); t.start()
            time.sleep(0.2); change()
        finally:
            PROCESS_LOCK.release()
        t.join(5); return result

    def test_unbound_owner_queued_request_is_dropped(self):
        def unbind():
            self.db.set_setting('bridge_owner:qq', '99999')
            self.db.add_message('user', 'NEW-OWNER-PRIVATE-SYNTHETIC', self.cid)
        before = len(self.db.recent_messages(50, self.cid))
        result = self.queued(lambda: self.ask(OWNER, '在吗'), unbind)
        self.assertEqual(result['reason'], 'not_allowed'); self.assertEqual(self.client.calls, [])   # 模型零调用
        self.assertEqual(len(self.db.recent_messages(50, self.cid)), before+1)                     # 只有 unbind 写的那条

    def test_group_removed_while_queued_is_dropped(self):
        result = self.queued(lambda: self.ask(STRANGER, '大家好', group=GROUP), lambda: self.db.set_setting('bridge_groups', []))
        self.assertEqual(result['reason'], 'not_allowed'); self.assertEqual(self.client.calls, [])
        self.assertEqual(self.db.recent_messages(10, self.cid, channel=f'qq:group:{GROUP}'), [])

    def test_closed_service_drops_queued_requests(self):
        def close(): self.service.closed = True   # Runtime 重启 / 关闭时 BridgeServer.close 会这样做
        result = self.queued(lambda: self.ask(OWNER, '在吗'), close)
        self.assertEqual(result['reason'], 'closed'); self.assertEqual(self.client.calls, [])


class GroupChimeTests(BridgeTests.__base__):
    """群里没叫她的消息：旁听（记进群聊记录），按活跃度插话；可以选择不说。"""
    setUp = BridgeTests.setUp; tearDown = BridgeTests.tearDown; ask = BridgeTests.ask

    def hear(self, sender, text, name='大壮'):
        return self.service.handle({'platform': 'qq', 'sender_id': sender, 'sender_name': name, 'group_id': GROUP, 'text': text, 'called': False})

    def setUp_rolls(self, rolls, now=1000.0):
        self.rolls = list(rolls); self.now = [now]
        self.service.chance = lambda: self.rolls.pop(0) if self.rolls else 0.99
        self.service.clock = lambda: self.now[0]

    def group_rows(self):
        return self.db.recent_messages(50, self.cid, channel=f'qq:group:{GROUP}')

    def test_overheard_messages_are_recorded_without_calling_the_model(self):
        self.db.set_setting('bridge_chime', 'off')
        self.assertEqual(self.hear(STRANGER, '今天好热')['reason'], 'observed')
        self.assertEqual(self.hear(OWNER, '开空调啊', name='阿青')['reason'], 'observed')
        self.assertEqual(self.client.calls, [])
        self.assertEqual([r['content'] for r in self.group_rows()], ['大壮#1：今天好热', '【爹】阿青#2：开空调啊'])
        self.assertEqual(self.db.recent_messages(10, self.cid), [])   # 桌面频道不变
        # 后来被 @ 时，她看得到刚才大家聊了什么
        self.ask(STRANGER, '你觉得呢', group=GROUP, name='大壮')
        self.assertIn('今天好热', ' '.join(t.content for t in self.client.calls[-1][0]))

    def test_mentioning_her_name_counts_as_calling(self):
        self.db.set_setting('bridge_chime', 'off')
        result = self.hear(STRANGER, '鲸娘你吃饭了没')
        self.assertEqual(result['reason'], 'ok'); self.assertEqual(result['reply'], '好呀')

    def test_old_plugin_without_called_field_is_treated_as_called(self):
        self.assertEqual(self.ask(STRANGER, '在吗', group=GROUP)['reason'], 'ok')

    def test_chime_needs_enough_messages_cooldown_and_luck(self):
        self.setUp_rolls([0.1, 0.9, 0.1])   # often：概率 0.3，至少两条新消息，间隔 90 秒
        self.assertEqual(self.hear(STRANGER, '第一条')['reason'], 'observed')   # 才一条，不插话（不掷骰子）
        result = self.hear(STRANGER, '第二条')
        self.assertEqual((result['reason'], result['reply']), ('chime', '好呀'))
        self.assertIn('没有叫你', self.client.calls[-1][1]); self.assertIn('【不说】', self.client.calls[-1][1])
        self.hear(STRANGER, '第三条'); self.hear(STRANGER, '第四条')
        self.assertEqual(len(self.client.calls), 1)                              # 冷却中
        self.now[0] += 100
        self.assertEqual(self.hear(STRANGER, '第五条')['reason'], 'observed')   # 掷到 0.9，没插
        self.assertEqual(self.hear(STRANGER, '第六条')['reason'], 'chime')
        self.db.set_setting('bridge_chime', 'off'); self.now[0] += 1000
        self.hear(STRANGER, 'a'); self.hear(STRANGER, 'b')
        self.assertEqual(len(self.client.calls), 2)

    def test_silence_marker_sends_nothing_and_leaves_no_trace(self):
        self.setUp_rolls([0.0])
        self.client.generate_reply_stream = lambda history, prompt, cancellation=None: iter(['【不说】'])
        self.hear(STRANGER, '一'); result = self.hear(STRANGER, '二')
        self.assertEqual((result['reply'], result['reason']), ('', 'chime_silent'))
        self.assertFalse(any(r['role'] == 'assistant' for r in self.group_rows()))
        self.assertIsNone(self.db.get_setting(f'bridge_last_spoke:qq:group:{GROUP}', None))   # 没说话不算冷却

    def test_chime_daily_cap_and_quota(self):
        self.setUp_rolls([0.0]*10)
        self.db.set_setting('bridge_chime_daily', 1)
        self.hear(STRANGER, '一'); self.assertEqual(self.hear(STRANGER, '二')['reason'], 'chime')
        self.now[0] += 1000; self.hear(STRANGER, '三')
        self.assertEqual(self.hear(STRANGER, '四')['reason'], 'observed')     # 今天插话次数用完
        self.db.set_setting('bridge_chime_daily', 10); self.quota.state = 'stop'
        self.assertEqual(self.hear(STRANGER, '五')['reason'], 'observed')     # 额度用完也不插话
        self.assertEqual(len(self.client.calls), 1)

    def test_overhearing_does_not_wait_while_she_is_talking(self):
        self.setUp_rolls([0.0]*10)
        self.service.lock.acquire()
        try:
            self.hear(STRANGER, '一'); result = self.hear(STRANGER, '二')   # 锁被占着也立刻返回，只记录不插话
        finally:
            self.service.lock.release()
        self.assertEqual(result['reason'], 'observed'); self.assertEqual(len(self.group_rows()), 2)

    def test_removed_group_is_not_overheard(self):
        self.db.set_setting('bridge_groups', [])
        self.assertEqual(self.hear(STRANGER, '一')['reason'], 'not_allowed'); self.assertEqual(self.group_rows(), [])


class ChimeBudgetAndRevocationTests(BridgeTests.__base__):
    """Codex 审计 PR #9 的两处 P2：插话也受每日公开回复总上限；旁听记录的最终授权和写入在同一个同步边界里。"""
    setUp = BridgeTests.setUp; tearDown = BridgeTests.tearDown; ask = BridgeTests.ask
    hear = GroupChimeTests.hear; setUp_rolls = GroupChimeTests.setUp_rolls; group_rows = GroupChimeTests.group_rows
    OTHER = '40004'

    def hear_in(self, group, text, sender=STRANGER, name='大壮'):
        return self.service.handle({'platform': 'qq', 'sender_id': sender, 'sender_name': name, 'group_id': group, 'text': text, 'called': False})

    def counts(self):
        day = self.service.today().isoformat()
        return (self.db.get_setting(f'bridge_public_count:{day}', 0), self.db.get_setting(f'bridge_chime_count:{day}', 0))

    def test_public_daily_zero_blocks_chime(self):
        self.setUp_rolls([0.0]*10); self.db.set_setting('bridge_public_daily', 0)
        self.hear(STRANGER, '一'); self.assertEqual(self.hear(STRANGER, '二')['reason'], 'observed')
        self.assertEqual(self.client.calls, []); self.assertEqual(self.counts(), (0, 0))   # 也没白占插话次数

    def test_called_replies_and_chimes_share_the_public_limit(self):
        self.setUp_rolls([0.0]*10); self.db.set_setting('bridge_public_daily', 1)
        self.assertEqual(self.ask(STRANGER, '在吗', group=GROUP)['reason'], 'ok')          # 被叫用掉唯一一条
        self.now[0] += 1000; self.hear(STRANGER, '一'); self.hear(STRANGER, '二')
        self.assertEqual(self.hear(STRANGER, '三')['reason'], 'observed')
        self.assertEqual(len(self.client.calls), 1); self.assertEqual(self.counts(), (1, 0))

    def test_public_limit_is_shared_across_groups_and_counts_chimes(self):
        self.setUp_rolls([0.0]*10); self.db.set_setting('bridge_public_daily', 1)
        self.db.set_setting('bridge_groups', [GROUP, self.OTHER])
        self.hear_in(GROUP, '一'); self.assertEqual(self.hear_in(GROUP, '二')['reason'], 'chime')
        self.hear_in(self.OTHER, '一'); self.assertEqual(self.hear_in(self.OTHER, '二')['reason'], 'observed')   # 另一个群也没额度了
        self.assertEqual(self.ask(STRANGER, '在吗', group=self.OTHER)['reason'], 'daily_limit')            # 被叫同样
        self.assertEqual(self.counts(), (1, 1))

    def test_chime_cap_reached_does_not_use_public_budget(self):
        self.setUp_rolls([0.0]*10); self.db.set_setting('bridge_chime_daily', 0)
        self.hear(STRANGER, '一'); self.hear(STRANGER, '二')
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.ask(STRANGER, '在吗', group=GROUP)['reason'], 'ok')

    # ---- 撤销权限与写入的同步边界 ----
    def blocked_hear(self, change, sender=STRANGER, name='大壮'):
        """另一个连接先占住数据库写锁（模拟旁听线程在等编号 / 等数据库），期间改设置后提交，再放行旁听线程。"""
        import sqlite3 as sq
        self.db.set_setting('bridge_chime', 'off')
        blocker = sq.connect(self.db.database_path, timeout=5, isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
        out = {}
        worker = threading.Thread(target=lambda: out.update(r=self.hear(sender, '刚才那条', name=name)))
        worker.start(); time.sleep(0.3)
        self.assertTrue(worker.is_alive())                       # 确实卡在写事务前
        change(blocker); blocker.execute('COMMIT'); blocker.close()
        worker.join(5); self.assertFalse(worker.is_alive())
        return out['r']

    @staticmethod
    def put(connection, key, value):
        connection.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def test_group_removed_while_waiting_is_not_recorded(self):
        result = self.blocked_hear(lambda c: self.put(c, 'bridge_groups', []))
        self.assertEqual(result['reason'], 'not_allowed'); self.assertEqual(self.group_rows(), [])

    def test_owner_unbound_while_waiting_gets_no_dad_label(self):
        result = self.blocked_hear(lambda c: self.put(c, 'bridge_owner:qq', ''), sender=OWNER, name='阿青')
        self.assertEqual(result['reason'], 'observed')
        self.assertEqual([r['content'] for r in self.group_rows()], ['阿青#1：刚才那条'])

    def test_closed_while_waiting_is_not_recorded(self):
        from aicompanion.bridge.service import RECORD_LOCK
        self.db.set_setting('bridge_chime', 'off')
        out = {}
        with RECORD_LOCK:                                         # close() 先拿到边界
            worker = threading.Thread(target=lambda: out.update(r=self.hear(STRANGER, '刚才那条')))
            worker.start(); time.sleep(0.2); self.assertTrue(worker.is_alive())
            self.service.closed = True
        worker.join(5)
        self.assertEqual(out['r']['reason'], 'closed'); self.assertEqual(self.group_rows(), [])

    def test_close_waits_for_an_in_flight_record_then_blocks_new_ones(self):
        import sqlite3 as sq
        self.db.set_setting('bridge_chime', 'off')
        blocker = sq.connect(self.db.database_path, timeout=5, isolation_level=None); blocker.execute('BEGIN IMMEDIATE')
        writer = threading.Thread(target=lambda: self.hear(STRANGER, '在写的那条')); writer.start(); time.sleep(0.3)
        closer = threading.Thread(target=self.service.close); closer.start(); time.sleep(0.2)
        self.assertTrue(closer.is_alive())                       # close 等在途的那条写完
        blocker.execute('COMMIT'); blocker.close()
        writer.join(5); closer.join(5); self.assertFalse(closer.is_alive())
        self.assertEqual(len(self.group_rows()), 1)
        self.assertEqual(self.hear(STRANGER, '之后的')['reason'], 'closed'); self.assertEqual(len(self.group_rows()), 1)


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


class EarlyV4Tests(unittest.TestCase):
    def test_first_t9_database_shape_upgrades(self):
        """试用过 T9 初版的库：user_version=4、有 channel、没有 sender。升级要备份、保留数据、能写新消息，重复打开也没问题。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'early.sqlite3'
            db = Database(path); db.initialize()
            db.add_message('user', '旧的群消息', 'emilia', channel='qq:group:30003')
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('ALTER TABLE chat_messages DROP COLUMN sender'); conn.execute('PRAGMA user_version=4'); conn.commit()
            for backup in Path(tmp).glob('*.backup-*'): backup.unlink()
            db = Database(path); db.initialize()
            self.assertEqual(len(list(Path(tmp).glob('early.backup-*.sqlite3'))), 1)
            rows = db.recent_messages(5, 'emilia', channel='qq:group:30003')
            self.assertEqual([(r['content'], r['sender']) for r in rows], [('旧的群消息', '')])
            db.add_message('user', '新消息', 'emilia', channel='qq:group:30003', sender='qq:1')
            Database(path).initialize()                                      # 重复打开
            self.assertEqual(len(list(Path(tmp).glob('early.backup-*.sqlite3'))), 1)
            with closing(sqlite3.connect(path)) as conn: self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 6)


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
