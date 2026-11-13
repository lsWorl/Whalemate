"""群话题的整理、使用、撤销和投递。全部使用临时库、可控时钟和假 QQ。"""
import json
from datetime import datetime, timezone
from unittest.mock import patch

from aicompanion.brain.scheduler import GROUP_GAP_SECONDS, ProactiveScheduler
from aicompanion.bridge.server import runtime
from aicompanion.llm.base import ChatTurn
from aicompanion.services import budget
from aicompanion.services.proactive_service import ProactiveMessageService
from aicompanion.services.promises import Promises
from aicompanion.services.circle_topics import CircleTopics, INTENT, switch_key
from aicompanion.storage.schema import SCHEMA_VERSION
from test_qq_proactive import Base


class CircleTests(Base):
    def setUp(self):
        super().setUp()
        self.cid = self.character.id
        self.scope, self.other = 'qq:group:30003', 'qq:group:40004'
        self.now = datetime(2032, 4, 7, 14).timestamp()
        self.clock = lambda: self.now
        self.topics = CircleTopics(self.db, self.clock)
        self.scheduler = ProactiveScheduler(self.db, clock=self.clock)
        self.service.clock = self.clock
        self.service.chance = lambda: 0
        self.db.set_setting('bridge_groups', ['30003', '40004'])
        runtime.last_heartbeat['astrbot'] = 'plugin-0.11.0'

    def message(self, text, age=0, scope=None, sender='qq:20002', role='user'):
        channel = scope or self.scope
        mid = self.db.add_message(role, text, self.cid, source='qq', channel=channel, sender=sender)
        stamp = datetime.fromtimestamp(self.now-age, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with self.db.transaction() as c:
            c.execute('UPDATE chat_messages SET created_at=? WHERE id=?', (stamp, mid))
        return mid

    def store(self, channel, name, ids, summary='反复推敲布局'):
        rows = self.db.recent_messages(40, self.cid, channel=channel)
        self.topics.collect(self.cid, channel, rows, [dict(name=name, summary=summary, source_ids=ids)])

    def repeated(self):
        ids = [self.message('谈谈布局', 90000), self.message('再来一盘', 3600)]
        self.store(self.scope, '对局', ids)
        return ids

    def choose(self):
        return self.scheduler.next(self.cid, 0, 9999, group_ready=self.service.group_ready, circle_ready=self.service.circle_ready)

    def send(self, item, target='30003'):
        rid = self.scheduler.reserve(self.cid, item)
        self.assertTrue(rid)
        turn = self.service.proactive_qq(INTENT, group=target, on_queued=lambda t: self.scheduler.attach(rid,t,'qq'),
                                         still_valid=lambda: self.scheduler.still_valid(item),
                                         on_done=lambda result: self.scheduler.settle_delivery(self.cid,rid,item,result,'qq'))
        if not turn:
            self.scheduler.release(rid)
        return turn

    def enable_circle(self):
        self.repeated()
        self.db.set_setting(switch_key(self.cid, self.scope), True)
        return self.choose()

    def add_promise(self, scope, label):
        service = Promises(self.db, self.clock)
        due = datetime.fromtimestamp(self.now + 60).strftime('%Y-%m-%d %H:%M')
        create, error = service.prepare(self.cid, self.controller.relationships.owner_person(), scope,
                                        'text' if scope == 'desktop' else 'qq', label, '', dict(due=due,title=label))
        self.assertFalse(error)
        self.db.locked(create)

    def acknowledge(self, turn):
        parcel, = self.service.take_outbox()
        self.assertEqual(parcel['id'], turn)
        self.assertTrue(self.service.outbox_claim(dict(id=turn, astrbot='plugin-0.11.0'))['allowed'])
        self.assertTrue(self.service.outbox_ack(dict(id=turn, status='sent', sent=1))['ok'])

    def test_unanswered_circle_leaves_owner_promise_due_and_reply_window_open(self):
        self.add_promise('desktop', '主人的到期约定')
        turn = self.send(self.enable_circle())
        self.acknowledge(turn)
        self.now += 60
        self.db.initialize()
        self.db.set_setting('proactive_daily_limit', 1)
        self.assertEqual(self.scheduler.today_count(self.cid), 0)
        self.assertEqual(self.scheduler.today_count(self.cid, self.scope), 1)
        self.assertEqual(self.scheduler.last(self.cid, self.scope)['status'], 'sent')
        owner = self.choose()
        self.assertEqual((owner.source, owner.payload['scope']), ('promise', 'desktop'))
        rid = self.scheduler.reserve(self.cid, owner)
        self.assertTrue(self.scheduler.show_desktop_promise(self.cid, rid, owner.key))
        self.assertEqual(self.scheduler.today_count(self.cid), 1)
        # 主人收到提醒之后，群友才回应原先那句；两个会话各自认领回应。
        self.now += 2
        self.message('我也这么想')
        self.scheduler.settle(self.cid)
        self.assertEqual(self.scheduler.last(self.cid, self.scope)['status'], 'answered')
        self.assertEqual(self.scheduler.last(self.cid)['status'], 'sent')

    def test_unanswered_circle_leaves_evening_greeting_on_time(self):
        self.now = datetime(2032,4,7,19,59).timestamp()
        self.acknowledge(self.send(self.enable_circle()))
        self.scheduler.greetings = ProactiveMessageService(self.db, clock=lambda: datetime.fromtimestamp(self.now),
                                                          pace=lambda cid: 1.0)
        self.db.set_setting('proactive_daily_limit', 1)
        self.now += 60
        greeting = self.choose()
        self.assertEqual((greeting.source,greeting.key), ('greeting','2032-04-07:evening'))
        self.assertTrue(self.scheduler.record(self.cid, greeting))
        self.assertEqual(self.scheduler.today_count(self.cid), 1)
        self.assertEqual(self.scheduler.last(self.cid,self.scope)['status'], 'sent')

    def test_pending_circle_and_owner_or_other_group_can_reserve_together(self):
        item = self.enable_circle()
        for scope in (self.scope, self.other, 'desktop'):
            self.add_promise(scope, scope)
        turn = self.send(item)
        self.now += 60
        self.assertTrue(self.scheduler.pending(self.cid,self.scope))
        self.assertFalse(self.scheduler.pending(self.cid))
        owner = self.choose()
        self.assertEqual(owner.payload['scope'], 'desktop')
        owner_id = self.scheduler.reserve(self.cid, owner)
        self.assertTrue(owner_id)
        other = self.choose()
        self.assertEqual(other.payload['scope'], self.other)
        self.assertTrue(self.scheduler.reserve(self.cid, other))
        self.assertIsNone(self.choose())
        active = Promises(self.db,self.clock).due(self.cid)
        self.assertEqual(active['scope'], self.scope)
        self.assertFalse(self.scheduler.reserve(self.cid, self.scheduler._promise(active)))
        self.acknowledge(turn)
        self.assertTrue(self.scheduler.show_desktop_promise(self.cid, owner_id, owner.key))

    def test_group_promises_share_circle_gap_and_daily_limit_only_in_original_group(self):
        self.db.set_setting('proactive_daily_limit', 0)
        self.db.set_setting('proactive_gap_minutes', 600)
        circle = self.enable_circle()
        self.acknowledge(self.send(circle))
        self.now += 1
        self.message('群友已经接话')
        self.add_promise(self.scope, '本群第二条')
        self.add_promise(self.scope, '本群第三条')
        self.now += GROUP_GAP_SECONDS - 2
        self.assertIsNone(self.choose())
        self.now += 1
        promise = self.choose()
        self.assertEqual((promise.source,promise.payload['scope']), ('promise',self.scope))
        rid = self.scheduler.reserve(self.cid, promise)
        self.assertTrue(rid)
        self.assertFalse(self.scheduler.show_desktop_promise(self.cid, rid, promise.key))
        self.scheduler.confirm(self.cid, rid, promise, channel='qq')
        self.now += 1
        self.message('收到第二条')
        self.now += GROUP_GAP_SECONDS
        self.assertIsNone(self.choose())
        self.assertEqual(self.scheduler.today_count(self.cid,self.scope), 2)
        self.assertEqual(self.scheduler.today_count(self.cid), 0)
        self.add_promise(self.other, '别的群')
        self.now += 60
        self.assertEqual(self.choose().payload['scope'], self.other)
        self.now = datetime(2032,4,8,9).timestamp()
        self.assertEqual(self.scheduler.today_count(self.cid,self.scope), 0)
        self.assertEqual(self.choose().payload['scope'], self.scope)

    def test_legacy_scope_backfill_preserves_circle_and_both_types_of_promises(self):
        turn = self.send(self.enable_circle())
        self.acknowledge(turn)
        for scope in (self.scope, 'desktop'):
            self.add_promise(scope, scope)
        self.now += 60
        rows = Promises(self.db,self.clock).due_rows(self.cid)
        with self.db.transaction() as c:
            for row in rows:
                c.execute("INSERT INTO outreach_log(character_id,source,key,channel,status,sent_at) "
                          "VALUES (?,'promise',?,'qq','answered',?)",
                          (self.cid, f"promise:{row['id']}:1", self.now))
            c.execute('UPDATE promises SET revision=2')
            c.execute('DROP INDEX ix_outreach_scope')
            c.execute('ALTER TABLE outreach_log DROP COLUMN scope')
        self.db.initialize()
        self.db.initialize()
        self.assertEqual(self.scheduler.today_count(self.cid), 1)
        self.assertEqual(self.scheduler.today_count(self.cid,self.scope), 2)
        self.assertEqual(self.scheduler.last(self.cid,self.scope)['source'], 'promise')
        with self.db.transaction() as c:
            self.assertEqual(c.execute("SELECT scope FROM outreach_log WHERE turn_id=?", (turn,)).fetchone()[0], self.scope)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_one_model_call_validates_group_sources_and_participants(self):
        ids = [self.message('在讨论棋局', 90000), self.message('棋局继续', 3600)]
        foreign = self.message('别群消息', scope=self.other)
        robot = self.message('她自己说的', role='assistant')
        duplicate = dict(content='本群在复盘决赛', fact_key='错误复制的话题', category='event', source_ids=ids)
        self.model.reply = json.dumps(dict(memories=[duplicate], topics=[
            dict(name='棋局', summary='本群在复盘决赛', source_ids=ids),
            dict(name='错群', summary='不可写', source_ids=[foreign, ids[0]]),
            dict(name='错人', summary='不可写', source_ids=[robot])]))
        self.db.set_setting('bridge_memory_every', 2)
        self.assertTrue(self.service.maybe_remember(self.cid, self.scope, background=False))
        self.assertEqual(len(self.model.prompts), 1)
        self.assertFalse(self.db.recent_memories(character_id=self.cid))
        self.assertIn('topics', self.model.prompts[0])
        row, = self.topics.rows(self.cid, self.scope)
        self.assertEqual((row['name'], row['summary'], row['days']), ('棋局', '本群在复盘决赛', 2))
        self.store(self.scope, '棋局', ids)
        self.assertEqual(row['heat'], self.topics.rows(self.cid, self.scope)[0]['heat'])
        sources = self.topics.sources(self.cid, self.scope, row['id'])
        self.assertEqual({s['id'] for s in sources}, set(ids))
        self.assertEqual({s['person_id'] for s in sources}, {ids[0]})
        self.store(self.other, '棋局', [foreign])
        other, = self.topics.rows(self.cid, self.other)
        self.assertNotEqual(self.topics.sources(self.cid, self.other, other['id'])[0]['person_id'], ids[0])
        self.assertFalse(self.topics.sources(self.cid, self.other, row['id']))
        self.now += 7 * 86400
        self.assertAlmostEqual(row['heat']/2, self.topics.rows(self.cid, self.scope)[0]['heat'])

    def test_no_model_keyword_fallback_respects_group_lexicon(self):
        self.message('阿明#2：来排一把星露谷', 92000)
        self.message('小雨#3：下次还玩星露谷吧', 3600)
        with self.db.transaction() as c:
            tid = c.execute("INSERT INTO lexicon_terms(term,created_at,updated_at) VALUES ('星露谷',0,0)").lastrowid
            c.execute('INSERT INTO lexicon_sightings(term_id,channel,first_at,last_at) VALUES (?,?,0,0)', (tid,self.scope))
        self.db.set_setting('bridge_memory_every', 2)
        with patch.object(self.controller.router, 'ready', return_value=False):
            self.assertTrue(self.service.maybe_remember(self.cid, self.scope, background=False))
        self.assertFalse(self.model.prompts)
        self.assertIn('星露谷', [r['name'] for r in self.topics.rows(self.cid, self.scope)])

    def test_prompt_scopes_and_topics_yield_to_history(self):
        self.repeated()
        self.store(self.other, '另一群的专属词', [self.message('无关', scope=self.other)], '另群在筹办聚会')
        build = self.controller.context.build_with_sections
        _, prompt, sections = build(self.character, '你好', self.scope, 'qq')
        self.assertIn('反复推敲布局', prompt)
        self.assertNotIn('另一群的专属词', prompt)
        for scope, scene in [(self.other, 'qq'), ('desktop','pet'), ('qq:user:20002','qq')]:
            self.assertNotIn('反复推敲布局', build(self.character, '', scope, scene)[1])
        history = [ChatTurn('user', '甲'*100), ChatTurn('assistant','乙'), ChatTurn('user','今天呢')]
        extra = '\n本轮要求'
        total = len(prompt+extra) + sum(len(t.content) for t in history)
        with patch.dict(budget.LIMITS, qq=total-1):
            kept, trimmed = self.controller._fit(self.cid,'qq',history,prompt+extra,sections,dict(base=len(prompt)))
        self.assertEqual(kept, history)
        self.assertTrue(trimmed.endswith(extra))
        self.assertNotIn('反复推敲布局', trimmed)
        self.db.set_setting('bridge_groups', ['40004'])
        self.assertNotIn('反复推敲布局', build(self.character,'',self.scope,'qq')[1])

    def test_delete_clear_tombstones_keep_originals_and_other_groups(self):
        ids = self.repeated()
        self.store(self.scope, '另一个话题', ids)
        self.store(self.other, '别群话题', [self.message('另一边', scope=self.other)])
        topic = next(r for r in self.topics.rows(self.cid,self.scope) if r['name']=='对局')
        self.topics.remove(self.cid, self.scope, topic['id'])
        self.store(self.scope, '同义改名', ids)
        self.assertEqual([r['name'] for r in self.topics.rows(self.cid,self.scope)], ['另一个话题'])
        self.topics.remove(self.cid,self.scope)
        self.db.initialize()
        self.store(self.scope, '重启后重试', ids)
        self.assertFalse(self.topics.rows(self.cid,self.scope))
        self.assertEqual(len(self.db.recent_messages(40,self.cid,channel=self.scope)), 2)
        self.assertEqual(len(self.topics.rows(self.cid,self.other)), 1)
        self.store(self.scope, '后来重新讨论', [self.message('新原话')])
        self.assertEqual(len(self.topics.rows(self.cid, self.scope)), 1)

    def test_cold_recent_days_opt_in_quiet_daily_gap_and_chime(self):
        self.repeated()
        self.assertIsNone(self.choose())
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        item = self.choose()
        self.assertEqual((item.source, item.payload['scope']), ('circle', self.scope))
        for key, value in [('bridge_chime','off'), ('quiet_start',13),
                           ('bridge_groups',['40004']), ('bridge_last_spoke:'+self.scope,self.now-5)]:
            previous = self.db.get_setting(key)
            self.db.set_setting(key, value)
            self.assertIsNone(self.choose(), key)
            with self.db.transaction() as c:
                c.execute('DELETE FROM app_settings WHERE key=?', (key,))
            if previous is not None:
                self.db.set_setting(key, previous)
        self.message('刚又有人接话', 3)
        self.assertIsNone(self.choose())
        self.now += 8 * 86400
        self.assertIsNone(self.choose())

    def test_orig_group_only_quota_silence_and_revoke_after_dequeue(self):
        self.repeated()
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        item = self.choose()
        self.assertIsNone(self.send(item,'40004'))
        self.assertIsNone(self.send(item,''))
        for key in ('bridge_public_daily','bridge_chime_daily'):
            self.db.set_setting(key,0)
            self.assertIsNone(self.send(item))
            self.db.set_setting(key,100)
        self.assertFalse(self.model.prompts)
        self.model.reply = '【不说】'
        self.assertIsNone(self.send(item))
        self.assertFalse(any(r['role']=='assistant' for r in self.db.recent_messages(10,self.cid,channel=self.scope)))
        self.assertIsNone(self.choose())
        self.message('又发现了新的棋谱')
        self.now += 3600
        self.model.reply = '昨天那个残局，我想到另一个走法了'
        turn = self.send(item)
        self.assertTrue(turn)
        parcel, = self.service.take_outbox()
        self.assertEqual(parcel['group_id'], '30003')
        self.assertNotIn('user_id', parcel)
        self.db.set_setting('bridge_groups', [])
        self.assertFalse(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])
        self.assertFalse(self.db.recent_messages(1,self.cid,channel='desktop'))

    def test_sent_once_restart_no_mood_and_failed_reservation_release(self):
        self.repeated()
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        item = self.choose()
        rid = self.scheduler.reserve(self.cid,item)
        self.assertFalse(self.scheduler.reserve(self.cid,item))
        self.scheduler.release(rid)
        turn = self.send(item)
        parcel, = self.service.take_outbox()
        self.assertEqual(parcel['id'], turn)
        self.assertTrue(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])
        self.assertTrue(self.service.outbox_ack(dict(id=turn,status='sent',sent=1))['ok'])
        self.db.initialize()
        self.scheduler.reconcile(self.cid)
        self.assertIsNone(self.scheduler.last(self.cid))
        self.assertEqual(self.scheduler.last(self.cid, self.scope)['source'], 'circle')
        self.assertFalse(self.scheduler.reserve(self.cid,item))
        self.assertIsNone(self.choose())
        self.message('别群说的话', scope=self.other)
        self.now += 4 * 3600
        with patch('aicompanion.services.mood_service.MoodService.event') as mood:
            self.scheduler.settle(self.cid)
            mood.assert_not_called()
        self.assertEqual(self.scheduler.last(self.cid, self.scope)['status'],'dropped')
        self.assertIsNone(self.topics.due(self.cid, lambda group: True))

    def test_topics_after_forgetting_use_only_new_evidence(self):
        self.repeated()
        mid = self.message('以前说过的事')
        self.db.upsert_memory('旧事实', self.cid, '旧条目', source_ids=[mid], scope='qq')
        old = self.db.recent_memories(character_id=self.cid)[0]
        self.db.delete_memory(old['id'], self.cid)
        self.assertFalse(self.topics.prompt(self.cid, self.scope))
        self.message('新人说：明天来打羽毛球')
        self.message('大家还想约羽毛球')
        self.db.set_setting('bridge_memory_every', 2)
        self.service.maybe_remember(self.cid, self.scope, background=False)
        self.assertIn('羽毛球', self.topics.prompt(self.cid,self.scope))
        self.assertNotIn('反复推敲布局', self.topics.prompt(self.cid,self.scope))
        self.assertEqual(self.model.prompts, [])

    def test_throttled_group_gives_way_and_new_activity_withdraws_queue(self):
        self.repeated()
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        self.db.set_setting(switch_key(self.cid,self.other), True)
        ids = [self.message('别群讨论', 93000, self.other), self.message('今天接着聊', 4000, self.other)]
        self.store(self.other, '周末出行', ids, '商量出发地点')
        self.message('刚在第一群说过话', 3500, role='assistant')
        choice = self.choose()
        self.assertEqual(choice.payload['scope'], self.other)
        turn = self.send(choice, '40004')
        self.assertTrue(self.service.take_outbox())
        self.message('这个群又聊起来了', scope=self.other)
        self.assertFalse(self.service.outbox_claim(dict(id=turn, astrbot='plugin-0.11.0'))['allowed'])

    def test_topic_reply_does_not_leak_via_desktop_glance(self):
        self.repeated()
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        self.model.reply = '只在原群说的见解'
        turn = self.send(self.choose())
        self.assertIn('反复推敲布局', self.model.prompts[-1])
        self.assertTrue(self.service.take_outbox())
        self.assertTrue(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])
        self.assertTrue(self.service.outbox_ack(dict(id=turn,status='sent',sent=1))['ok'])
        _, desktop = self.controller.context.build(self.character)
        self.assertNotIn(self.model.reply, desktop)

    def test_unknown_claim_is_not_replayed_after_restart(self):
        self.repeated()
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        item = self.choose()
        turn = self.send(item)
        self.service.take_outbox()
        self.assertTrue(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])
        self.assertTrue(self.service.outbox_ack(dict(id=turn,status='unknown',sent=0))['ok'])
        self.db.initialize()
        self.scheduler.reconcile(self.cid)
        self.assertIsNone(self.choose())
        self.assertFalse(self.scheduler.reserve(self.cid,item))

    def test_group_opt_out_and_delete_withdraw_queue(self):
        self.repeated()
        key = switch_key(self.cid,self.scope)
        self.db.set_setting(key, True)
        turn = self.send(self.choose())
        self.assertTrue(self.service.take_outbox())
        self.db.set_setting(key, False)
        self.assertFalse(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])
        self.db.set_setting(key, True)
        turn = self.send(self.choose())
        self.assertTrue(self.service.take_outbox())
        tid = self.topics.rows(self.cid,self.scope)[0]['id']
        self.topics.remove(self.cid,self.scope,tid)
        self.assertFalse(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])

    def test_overnight_delivery_counts_on_arrival_day(self):
        self.now = datetime(2032, 4, 7, 23, 59, 55).timestamp()
        self.db.set_setting('quiet_start', 0)
        self.db.set_setting('quiet_end', 0)
        self.repeated()
        self.db.set_setting(switch_key(self.cid,self.scope), True)
        turn = self.send(self.choose())
        self.assertTrue(self.service.take_outbox())
        self.assertTrue(self.service.outbox_claim(dict(id=turn,astrbot='plugin-0.11.0'))['allowed'])
        self.now += 10
        self.assertTrue(self.service.outbox_ack(dict(id=turn,status='sent',sent=1))['ok'])
        self.now += 3600
        self.db.initialize()
        self.scheduler.reconcile(self.cid)
        self.assertIsNone(self.topics.due(self.cid, lambda group: True))

    def test_deletion_during_analysis_discards_even_a_renamed_topic(self):
        self.repeated()
        new_id = self.message('后来又有人发言')
        def delayed(history, prompt, cancellation=None):
            self.topics.remove(self.cid, self.scope)
            yield json.dumps(dict(memories=[], topics=[dict(name='换个标题', summary='旧批次',
                                                            source_ids=[new_id])]))
        self.db.set_setting('bridge_memory_every', 2)
        with patch.object(self.model, 'generate_reply_stream', delayed):
            self.service.maybe_remember(self.cid, self.scope, background=False)
        self.assertFalse(self.topics.rows(self.cid, self.scope))

    def test_v19_migration_keeps_chat_and_memories(self):
        mid = self.message('迁移前的群消息')
        self.db.upsert_memory('旧记忆',self.cid,'旧键',source_ids=[mid],scope='qq')
        with self.db.transaction() as c:
            for table in ('circle_evidence','circle_topics','circle_tombstones'):
                c.execute('DROP TABLE '+table)
            c.execute('PRAGMA user_version=19')
        self.db.initialize()
        with self.db.transaction() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(self.db.recent_messages(1,self.cid,channel=self.scope)[0]['id'],mid)
        self.assertEqual(self.db.recent_memories(character_id=self.cid)[0]['content'],'旧记忆')
        self.assertFalse(self.topics.rows(self.cid,self.scope))
