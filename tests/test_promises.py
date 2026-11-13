"""临时库、可控本地时钟、真实聊天控制器和模拟 QQ 发件箱。"""
from datetime import datetime
import json

from aicompanion.brain.scheduler import ProactiveScheduler
from aicompanion.bridge.server import runtime
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.expression.stream import ExpressionStream
from aicompanion.services.promises import ASK, INTENT, Promises
from test_relationship import BridgeFixture, CID
from test_qq_proactive import Base as QQBase


class PromiseTests(BridgeFixture):
    def setUp(self):
        super().setUp()
        self.clock.now = datetime(2032,2,28,21,0).timestamp()
        self.store = self.controller.promises = Promises(self.db,self.clock)
        self.scheduler = ProactiveScheduler(self.db,clock=self.clock)
        self.owner = self.rel.owner_person()
        self.db.set_setting('bridge_groups',['30003','40004'])
        self.db.set_setting('quiet_start',23)
        self.db.set_setting('quiet_end',8)

    def chat(self, turn, query, reply):
        self.model.replies.append(reply)
        return ''.join(self.controller.generate(turn,self.character,CancellationToken(),query))

    def test_recognize_once_private_and_invalid_time(self):
        raw = '我记好了。<promise due="2032-02-29 09:00">提醒交报告</promise>'
        self.assertEqual(self.chat('a','明天九点提醒我',raw),'我记好了。')
        replay, _ = self.store.prepare(CID,self.owner,'desktop','text','a','明天九点提醒我',
                                       {'due':'2032-02-29 09:00','title':'提醒交报告'})
        self.db.locked(replay)
        row, = self.store.rows(CID)
        self.assertEqual((row['title'],row['person_id'],row['scope'],row['source'],row['turn_id'],row['due_at']),
                         ('提醒交报告',self.owner,'desktop','text','a',datetime(2032,2,29,9).timestamp()))
        self.assertEqual(self.db.recent_messages(1,CID)[0]['content'],'我记好了。')
        self.assertEqual(len(self.model.prompts),1)
        for i,value in enumerate(('2032-02-30 09:00','2032-02-27 09:00','以后')):
            shown = self.chat(str(i),'什么时候提醒？',f'我看看。<promise due="{value}">报告</promise>')
            self.assertIn(ASK,shown)
            self.assertNotIn('<promise',shown)
        self.assertEqual(len(self.store.rows(CID)),1)

    def test_stream_boundaries_literal_code_and_cancelled_turn(self):
        tag = '<promise condition="下次聊天">一起聊结果</promise>'
        sample = '这里是示例：\n```xml\n'+tag+'\n```\n好了。'
        for chunks in ([sample+tag],list(sample+tag)):
            parser = ExpressionStream()
            shown = ''.join(parser.feed(ch) for ch in chunks)+parser.finish()
            self.assertEqual(shown,sample)
            self.assertEqual(parser.promise_marker,{'condition':'下次聊天','title':'一起聊结果'})
        self.model.replies.append('好的。'+tag)
        generator = self.controller.generate('interrupted',self.character,CancellationToken(),'下次告诉我')
        next(generator)
        generator.close()
        self.assertEqual(self.store.rows(CID),[])

    def test_quiet_gap_unanswered_retry_restart_and_expire(self):
        self.chat('first','凌晨约一下','好。<promise due="2032-02-29 01:00">报告</promise>')
        self.clock.now = datetime(2032,2,29,1).timestamp()
        self.assertIsNone(self.scheduler.next(CID,0,9999))
        self.clock.now = datetime(2032,2,29,8).timestamp()
        item = self.scheduler.next(CID,0,9999)
        self.assertEqual(item.source,'promise')
        rid = self.scheduler.reserve(CID,item)
        self.assertFalse(self.scheduler.reserve(CID,item))
        self.scheduler.attach(rid,'first-attempt')
        self.assertTrue(self.store.reminder(CID,'desktop','first-attempt'))
        self.assertFalse(self.store.reminder(CID,'qq:group:30003','first-attempt'))
        self.scheduler.settle_delivery(CID,rid,item,False,'desktop')
        retry = self.scheduler.next(CID,0,9999)
        self.assertEqual(retry.key,item.key)
        rid = self.scheduler.reserve(CID,retry)
        self.scheduler.attach(rid,'retry')
        self.assertTrue(self.scheduler.show_desktop_promise(CID,rid,retry.key))
        self.scheduler.settle_delivery(CID,rid,retry,False,'desktop')
        self.db.initialize()
        self.assertEqual(self.store.rows(CID)[0]['status'],'done')
        self.assertIsNone(ProactiveScheduler(self.db,clock=self.clock).next(CID,0,9999))
        self.chat('second','提醒我','好。<promise due="2032-02-29 08:01" until="2032-02-29 09:00">开会</promise>')
        self.clock.advance(seconds=4000)
        self.assertIsNone(self.scheduler.next(CID,0,9999))
        self.store.due(CID)
        self.assertEqual(self.store.rows(CID)[0]['status'],'expired')

    def test_conditions_cancel_edit_and_authority(self):
        self.chat('owner','下次告诉我','好。<promise condition="下次聊天">私下的结果</promise>')
        entry = self.store.rows(CID)[0]
        self.assertIsNone(entry['due_at'])
        self.model.replies.append('在呢。')
        self.ask('10001','在吗','30003')
        self.assertNotIn('私下的结果',self.model.prompts[-1])
        self.assertIsNone(self.store.rows(CID)[0]['due_at'])
        self.chat('again','在吗','在。')
        self.assertIsNotNone(self.store.rows(CID)[0]['due_at'])
        self.assertTrue(self.store.change(CID,entry['id'],'新标题',condition='聊到：报告'))
        self.chat('topic1','天气','晴天。')
        self.assertIsNone(self.store.rows(CID)[0]['due_at'])
        self.chat('topic2','报告写完了','好。')
        self.assertIsNotNone(self.store.rows(CID)[0]['due_at'])
        self.chat('cancel','不用提醒了','好，那就取消。')
        self.assertEqual(self.store.rows(CID)[0]['status'],'cancelled')
        self.model.replies.append('记好了。<promise condition="下次聊天">原群自己的约定</promise>')
        wire = self.ask('20002','下次说结果','30003')
        self.assertNotIn('原群自己的约定',json.dumps(wire,ensure_ascii=False))
        row = self.store.rows(CID)[0]
        self.assertEqual((row['scope'],row['source']),('qq:group:30003','qq'))
        self.assertNotEqual(row['person_id'],self.owner)
        for sender,group in [('20002','40004'),('10001','30003'),('10001','')]:
            self.model.replies.append(f'好的。<promise cancel="{row["id"]}"/>')
            self.ask(sender,'取消提醒',group)
            self.assertNotIn('原群自己的约定',self.model.prompts[-1])
            self.assertEqual(self.store.rows(CID)[0]['status'],'pending')
        self.model.replies.append('取消好了。')
        self.ask('20002','不用提醒了','30003')
        self.assertEqual(self.store.rows(CID)[0]['status'],'cancelled')

    def test_duplicate_reservation_and_schedule_gates(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import timezone
        self.chat('due','稍后提醒我','好。<promise due="2032-02-28 21:01">原来的标题</promise>')
        self.clock.advance(seconds=120)
        self.db.set_setting('proactive_daily_limit',0)
        self.assertIsNone(self.scheduler.next(CID,0,9999))
        self.db.set_setting('proactive_daily_limit',5)
        item = self.scheduler.next(CID,0,9999)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: self.scheduler.reserve(CID,item), range(2)))
        self.assertEqual(sum(bool(v) for v in ids),1)
        rid = max(ids)
        self.scheduler.attach(rid,'queued')
        pid = self.store.rows(CID)[0]['id']
        self.assertTrue(self.store.change(CID,pid,'改后的标题','2032-02-28 21:05'))
        self.assertFalse(self.scheduler.still_valid(item))
        self.assertFalse(self.store.reminder(CID,'desktop','queued'))
        self.scheduler.settle_delivery(CID,rid,item,False,'desktop')
        self.clock.advance(seconds=360)
        next_item=self.scheduler.next(CID,0,9999)
        self.assertNotEqual(next_item.key,item.key)
        from aicompanion.brain.scheduler import Outreach
        prior = Outreach('greeting','prior','问好')
        self.scheduler.record(CID,prior)
        self.assertIsNone(self.scheduler.next(CID,0,9999))
        stamp = datetime.fromtimestamp(self.clock()+1,timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')
        with self.db.transaction() as c:
            c.execute("INSERT INTO chat_messages(role,content,character_id,created_at) VALUES ('user','收到',?,?)",(CID,stamp))
        self.clock.advance(seconds=300)
        self.assertIsNone(self.scheduler.next(CID,0,9999))
        self.clock.advance(seconds=90*60)
        self.assertEqual(self.scheduler.next(CID,0,9999).key,next_item.key)

    def test_edit_keeps_deadline_and_validates_replacement(self):
        self.chat('until','周日提醒我','好。<promise due="2032-02-29 09:00" until="2032-02-29 12:00">报告</promise>')
        row, = self.store.rows(CID)
        self.assertTrue(self.store.change(CID,row['id'],'报告更新','2032-02-29 10:00'))
        self.assertEqual(self.store.rows(CID)[0]['expires_at'],row['expires_at'])
        with self.assertRaises(ValueError):
            self.store.change(CID,row['id'],'不应保存','2032-02-29 11:00',until='2032-02-29 10:30')
        self.assertEqual(self.store.rows(CID)[0]['title'],'报告更新')

    def test_overlong_markup_cannot_execute_nested_controls(self):
        p = ExpressionStream()
        text = '正文<promise due="2032-02-29 09:00">' + '长'*800 + '<leave reason="不要执行"/></promise>结尾'
        self.assertEqual(''.join(p.feed(ch) for ch in text)+p.finish(),'正文结尾')
        self.assertEqual(p.promise_marker,{})
        self.assertFalse(p.leave_reason)

    def test_ambiguous_cancel_asks_without_picking_an_id(self):
        for turn in ('one','two'):
            self.chat(turn,'约定'+turn,'好。<promise condition="下次聊天">'+turn+'</promise>')
        pid = self.store.rows(CID)[0]['id']
        text = self.chat('cancel','不用提醒了',f'好。<promise cancel="{pid}"/>')
        self.assertIn('哪一项',text)
        self.assertEqual([r['status'] for r in self.store.rows(CID)],['pending','pending'])

    def test_v15_data_migration(self):
        self.db.add_message('user','旧历史',CID)
        with self.db.transaction() as c:
            c.execute('DROP TABLE promises')
            c.execute('ALTER TABLE qq_outbox DROP COLUMN target')
            c.execute('PRAGMA user_version=15')
        self.db.initialize()
        self.assertEqual(self.db.recent_messages(1,CID)[0]['content'],'旧历史')
        self.assertEqual(self.store.rows(CID),[])
        with self.db.transaction() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],16)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())


class PromiseDeliveryTests(QQBase):
    def setUp(self):
        super().setUp()
        self.now = datetime(2032,4,7,9,0).timestamp()
        self.store = self.controller.promises = Promises(self.db,lambda:self.now)
        self.scheduler = ProactiveScheduler(self.db,clock=lambda:self.now)
        self.service.clock = lambda:self.now
        self.owner = self.controller.relationships.owner_person()
        self.db.set_setting('quiet_start',0)
        self.db.set_setting('quiet_end',0)
        self.cid = self.character.id
        self.scope = 'qq:group:30003'
        runtime.last_heartbeat['astrbot']='plugin-0.11.0'

    def due(self,scope):
        create, error = self.store.prepare(self.cid,self.owner,scope,'qq','origin','',
                                           {'due':'2032-04-07 09:01','title':'原渠道的独有标题'})
        self.assertFalse(error)
        self.db.locked(create)
        self.now += 180
        return self.scheduler.next(self.cid,0,9999)

    def send(self, item, group):
        rid = self.scheduler.reserve(self.cid,item)
        self.model.reply='该讨论报告啦。'
        return self.service.proactive_qq(INTENT,group=group,on_queued=lambda t:self.scheduler.attach(rid,t,'qq'),
                    still_valid=lambda:self.scheduler.still_valid(item),
                    on_done=lambda result:self.scheduler.settle_delivery(self.cid,rid,item,result,'qq'))

    def test_unknown_delivery_is_never_replayed_and_late_failure_releases(self):
        item = self.due('desktop')
        turn = self.send(item,'')
        self.service.take_outbox()
        self.assertTrue(self.claim(turn)['allowed'])
        self.assertTrue(self.service.outbox_ack({'id':turn,'status':'unknown','sent':0})['ok'])
        self.db.initialize()
        self.scheduler.reconcile(self.cid)
        self.assertIsNone(self.store.due(self.cid))
        self.assertFalse(self.store.rows(self.cid)[0]['editable'])
        self.assertTrue(self.service.outbox_ack({'id':turn,'status':'failed','sent':0})['ok'])
        self.scheduler.reconcile(self.cid)
        self.assertEqual(self.store.due(self.cid)['id'],self.store.rows(self.cid)[0]['id'])

    def test_original_group_only_claim_ack_restart_no_owner_required(self):
        self.db.set_setting('bridge_owner:qq','')
        item = self.due(self.scope)
        self.assertIsNone(self.scheduler.next(self.cid,0,9999,group_ready=lambda g:False))
        turn = self.send(item,'30003')
        self.assertTrue(turn)
        self.assertIn('原渠道的独有标题',self.model.prompts[-1])
        old = dict(runtime.last_heartbeat,astrbot='plugin-0.10.2')
        self.assertFalse(self.service.take_outbox(old))
        parcel, = self.service.take_outbox(runtime.last_heartbeat)
        self.assertEqual(parcel['group_id'],'30003')
        self.assertNotIn('user_id',parcel)
        self.assertTrue(self.service.outbox_claim({'id':turn,'astrbot':'plugin-0.11.0'})['allowed'])
        self.assertTrue(self.service.outbox_ack({'id':turn,'status':'sent','sent':1})['ok'])
        self.db.initialize()
        self.scheduler.reconcile(self.cid)
        self.assertEqual(self.store.rows(self.cid)[0]['status'],'done')
        self.assertIsNone(self.store.due(self.cid))
        self.assertFalse(self.db.recent_messages(1,self.cid,channel='desktop'))

    def test_permission_revoked_after_dequeue_and_cancel_after_retry(self):
        item = self.due(self.scope)
        turn = self.send(item,'30003')
        self.assertTrue(self.service.take_outbox())
        self.db.set_setting('bridge_groups',[])
        self.assertFalse(self.service.outbox_claim({'id':turn,'astrbot':'plugin-0.11.0'})['allowed'])
        self.db.set_setting('bridge_groups',['30003'])
        self.assertEqual(self.store.due(self.cid)['status'],'pending')
        retry = self.send(item,'30003')
        self.assertTrue(self.service.take_outbox())
        self.assertTrue(self.store.change(self.cid,self.store.rows(self.cid)[0]['id'],cancel=True))
        self.assertFalse(self.service.outbox_claim({'id':retry,'astrbot':'plugin-0.11.0'})['allowed'])
        self.assertIsNone(self.store.due(self.cid))

    def test_owner_promise_cannot_generate_for_group_or_changed_binding(self):
        item = self.due('desktop')
        self.assertIsNone(self.send(item,'30003'))
        self.scheduler.reconcile(self.cid)
        self.assertNotIn('原渠道的独有标题',''.join(self.model.prompts))
        turn=self.send(item,'')
        self.assertTrue(self.service.take_outbox())
        self.db.set_setting('bridge_owner:qq','80008')
        self.assertFalse(self.claim(turn)['allowed'])
