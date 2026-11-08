"""控制器、桥接和插件的端到端模拟；只替换平台网络，不发送真实 QQ。"""
import asyncio,base64,hashlib,re,tempfile,types,sys,unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from aicompanion.storage.database import Database
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.services.memory_service import MemoryService
from aicompanion.bridge.service import BridgeService
import test_astrbot_plugin as plugin_fixture

class Model:
    def __init__(self):self.position='after_text';self.pure=False;self.silent=False;self.calls=0;self.reply_text='好耶'
    def generate_reply_stream(self,history,prompt,cancellation):
        self.calls+=1;ids=re.findall(r'^(s\d+)｜',prompt,re.M)
        marker=f'<sticker id="{ids[0]}" position="{self.position}"/>' if ids else ''
        text='【不说】' if self.silent else ('' if self.pure else self.reply_text)
        yield marker+text if self.position=='before_text' else text+marker

class QQExpressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'synthetic.sqlite3');self.db.initialize()
        char=replace(DEFAULT_CHARACTER,id='deepseek-whale',name='鲸娘');self.db.add_character(char)
        self.model=Model();self.controller=ChatController(self.db,MemoryService(self.db),self.model);self.controller.character=char
        self.db.set_setting('bridge_owner:qq','10001');self.db.set_setting('bridge_groups',['30003']);self.db.set_setting('bridge_memory_every',0)
        self.service=BridgeService(self.db,self.controller)
        self.module=plugin_fixture.load_plugin()
        self.components=types.ModuleType('astrbot.api.message_components')
        self.components.Image=type('Image',(),{'fromBase64':staticmethod(lambda value:('image',value))})
        self.components.Plain=lambda value:('text',value)
    def tearDown(self):self.service.close();self.temp.cleanup()
    def ask(self,group='',version=1):
        return self.service.handle(dict(platform='qq',sender_id='10001',sender_name='合成',group_id=group,text='项目完成了',
             called=True,expression_version=version,bot_account_id='90009'))
    def handler(self,answer,event=None,plugin=None):
        event=event or plugin_fixture.Event('嘿');plugin=plugin or self.module.AICompanionBridge(None,{'token':'synthetic'})
        async def denied(payload):return {}
        async def ask(payload):return answer
        async def control(path,payload):
            return self.service.expression_claim(payload) if path=='claim' else self.service.expression_ack(payload)
        async def no_pause(text):pass
        plugin.avatar_permission=denied;plugin.ask_pet=ask;plugin.expression_control=control;plugin.pause=no_pause
        async def run():return [item async for item in plugin.on_message(event)]
        with mock.patch.dict(sys.modules,{'astrbot.api.message_components':self.components}):out=asyncio.run(run())
        return event,plugin,out
    def test_before_after_and_pure_order_actual_plugin_handler(self):
        for position,pure,expected in [('before_text',False,['image','text']),('after_text',False,['text','image']),('after_text',True,['image'])]:
            self.model.position=position;self.model.pure=pure
            result=self.ask();event,_,out=self.handler(result)
            # 表情包单独一条消息，文字另起（负责人要求多段 / 图文分条发）
            self.assertEqual(out,[]);self.assertEqual([m[1][0][0] for m in event.sent_messages],expected)
            delivery=result['expression']['attachments'][0]['delivery_id']
            self.assertEqual(self.controller.expression.delivery(delivery)['status'],'accepted')
            self.assertTrue(self.service.expression_ack({'delivery_id':delivery,'status':'accepted'})['ok'])
            self.db.set_setting('qq_meme_cooldown',0)
    def test_no_attachment_for_old_transport_and_suppressed_group(self):
        result=self.ask(version=0);self.assertNotIn('expression',result);self.assertEqual(result['reply'],'好耶')
        self.model.silent=True;result=self.ask(group='30003');self.assertTrue(result['silent'])
        self.assertFalse(result.get('expression',{}).get('attachments'))
    def test_group_revoked_after_generation_sends_nothing(self):
        answer=self.ask('30003');delivery=answer['expression']['attachments'][0]['delivery_id']
        self.db.set_setting('bridge_groups',[])
        event,_,out=self.handler(answer,event=plugin_fixture.Event('嘿',group='30003'))
        self.assertEqual(event.sent_messages,[]);self.assertEqual(out,[])
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery})['allowed'])
    def test_claim_is_bound_and_single_use(self):
        answer=self.ask();delivery=answer['expression']['attachments'][0]['delivery_id']
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery,'group_id':'evil'})['allowed'])
        self.assertTrue(self.service.expression_claim({'delivery_id':delivery})['allowed'])
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery})['allowed'])
        self.assertFalse(self.service.expression_ack({'delivery_id':'x'*32,'status':'accepted'})['ok'])
    def test_timeout_unknown_and_no_retry(self):
        answer=self.ask();delivery=answer['expression']['attachments'][0]['delivery_id']
        event=plugin_fixture.Event('嘿')
        sent=event.sent_messages
        async def timeout(message):   # 文字正常，图片这一条超时：结果未知，不重发
            if any(part[0]=='image' for part in message[1]):raise asyncio.TimeoutError()
            sent.append(message)
        event.send=timeout;_,plugin,_=self.handler(answer,event)
        self.assertEqual(self.controller.expression.delivery(delivery)['status'],'unknown')
        again,_,_=self.handler(answer,plugin=plugin);self.assertEqual(again.sent_messages,[])
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery})['allowed'])
    def test_explicit_platform_rejection_is_failed(self):
        answer=self.ask();delivery=answer['expression']['attachments'][0]['delivery_id']
        class ActionFailed(Exception):pass
        event=plugin_fixture.Event('嘿')
        async def reject(message):
            if any(part[0]=='image' for part in message[1]):raise ActionFailed()
        event.send=reject;self.handler(answer,event)
        self.assertEqual(self.controller.expression.delivery(delivery)['status'],'failed')
    def test_text_failure_before_image_stops_and_reports_not_attempted(self):
        # Codex 审计 P2：图在后时前面的文字失败 / 超时，就整条停下，图片不发，如实上报 cancelled（不伪造 accepted / unknown）
        class ActionFailed(Exception):pass
        for error in (ActionFailed(),asyncio.TimeoutError()):
            self.model.position='after_text';self.db.set_setting('qq_meme_cooldown',0)
            answer=self.ask();delivery=answer['expression']['attachments'][0]['delivery_id']
            event=plugin_fixture.Event('嘿');attempted=[]
            async def fail(message,error=error):attempted.append([p[0] for p in message[1]]);raise error
            event.send=fail;_,plugin,out=self.handler(answer,event)
            self.assertEqual(attempted,[['text']],type(error).__name__)
            self.assertEqual(self.controller.expression.delivery(delivery)['status'],'cancelled')
            self.assertEqual(self.controller.expression.traces(1)[-1]['stage'],'E_not_delivered')
    def test_middle_text_failure_stops_remaining_text_and_image(self):
        self.model.position='after_text';self.model.reply_text='一\n\n二\n\n三'
        answer=self.ask();self.assertEqual(answer.get('segments'),['一','二','三'])
        delivery=answer['expression']['attachments'][0]['delivery_id']
        class ActionFailed(Exception):pass
        event=plugin_fixture.Event('嘿');attempted=[]
        async def second_fails(message):
            attempted.append(message[1][0][1] if message[1][0][0]=='text' else 'image')
            if len(attempted)==2:raise ActionFailed()
        event.send=second_fails;self.handler(answer,event)
        self.assertEqual(attempted,['一','二'])
        self.assertEqual(self.controller.expression.delivery(delivery)['status'],'cancelled')
    def test_oversized_image_falls_back_to_text_and_no_claim(self):
        answer=self.ask();answer['expression']['attachments'][0]['data_uri']='data:image/png;base64,'+'A'*1500000
        event,_,out=self.handler(answer)
        self.assertFalse(event.sent_messages);self.assertEqual(out,[('plain','好耶')])
    def test_disabled_before_send_falls_back_without_image(self):
        answer=self.ask();self.db.set_setting('qq_memes_enabled',False)
        event,_,out=self.handler(answer)
        self.assertFalse(event.sent_messages);self.assertEqual(out,[('plain','好耶')])
    def test_original_brain_called_once_and_no_paths_in_candidates(self):
        answer=self.ask()
        self.assertEqual(self.model.calls,1);self.assertEqual(answer['expression']['decision']['action'],'text_and_sticker')
        self.assertEqual(len(answer['expression']['attachments']),1)

    def test_role_switch_and_expiry_reject_pending_old_image(self):
        answer=self.ask();delivery=answer['expression']['attachments'][0]['delivery_id']
        old=self.controller.character;self.controller.character=DEFAULT_CHARACTER
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery})['allowed'])
        self.controller.character=old
        with self.db.transaction() as c:c.execute('UPDATE expression_deliveries SET created_at=0 WHERE delivery_id=?',(delivery,))
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery})['allowed'])
    def test_late_ack_can_resolve_unknown_without_resending(self):
        answer=self.ask();delivery=answer['expression']['attachments'][0]['delivery_id']
        self.assertTrue(self.service.expression_claim({'delivery_id':delivery})['allowed'])
        self.assertTrue(self.service.expression_ack({'delivery_id':delivery,'status':'unknown'})['ok'])
        self.assertTrue(self.service.expression_ack({'delivery_id':delivery,'status':'accepted'})['ok'])
        self.assertFalse(self.service.expression_claim({'delivery_id':delivery})['allowed'])
