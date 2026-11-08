"""真实 ChatController，模型只替换流式字节；不另造聊天入口。"""
from pathlib import Path
from dataclasses import replace
import re,tempfile,unittest
from aicompanion.storage.database import Database
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.cancellation import CancellationToken,ReplyCancelled
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.services.memory_service import MemoryService
from aicompanion.expression.domain import ExpressionContext

class Model:
    def __init__(self):self.calls=0;self.mode='mixed';self.prompts=[]
    def generate_reply_stream(self,history,prompt,cancellation):
        self.calls+=1;self.prompts.append(prompt)
        ids=re.findall(r'"asset_id": "(st_[a-f0-9]{32})"',prompt)
        marker=f'<sticker asset_id="{ids[0]}" position="before_text"/>' if ids else ''
        text={'mixed':marker+'好耶！','pure':marker,'text':'恭喜呀','silent':'【不说】'+marker,'task':marker+'<task>写文件</task>'}.get(self.mode,'正常')
        for ch in text:yield ch

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database(Path(self.temp.name)/'synthetic.sqlite3');self.db.initialize()
        self.char=replace(DEFAULT_CHARACTER,id='deepseek-whale',name='鲸娘');self.db.add_character(self.char)
        self.model=Model();self.controller=ChatController(self.db,MemoryService(self.db),self.model)
        self.controller.character=self.char;self.events=[];self.token=CancellationToken()
    def tearDown(self):self.temp.cleanup()
    def run_turn(self,turn='g',proactive='',filter=None,early=False):
        ctx=ExpressionContext(turn,self.char.id,'desktop:'+self.char.id,early_before=early)
        result=''.join(self.controller.generate(turn,self.char,self.token,'太好啦',proactive,reply_filter=filter,
                    expression_context=ctx,on_expression_event=self.events.append))
        return result,self.events[-1].result
    def test_same_request_text_and_image_without_control_leak(self):
        text,result=self.run_turn()
        self.assertEqual(text,'好耶！');self.assertEqual(self.model.calls,1)
        self.assertEqual(result.decision.action,'text_and_sticker');self.assertEqual(len(result.attachments),1)
        self.assertNotIn('<sticker',self.db.recent_messages(5,self.char.id)[-1]['content'])
    def test_plain_text_allowed_with_same_candidates(self):
        self.model.mode='text';text,result=self.run_turn()
        self.assertEqual(text,'恭喜呀');self.assertFalse(result.attachments);self.assertEqual(self.model.calls,1)
    def test_pure_image_history_uses_confirmed_attachment_only(self):
        self.model.mode='pure';text,result=self.run_turn()
        self.assertEqual(text,'');self.assertEqual(result.decision.action,'sticker_only')
        self.assertEqual(len(self.db.recent_messages(5,self.char.id)),1)
        self.controller.expression.receipt(result.attachments[0].delivery_id,'displayed')
        history,prompt=self.controller.context.build(self.char)
        self.assertFalse(history)
        self.assertIn('历史附件回执',prompt)
        self.assertIn(result.attachments[0].alt_text,prompt)
        self.assertNotIn('[角色已发送表情',prompt)
        self.db.set_delivered_reply('g',self.char.id,'','cancelled')
        self.assertTrue(self.controller.expression.history('g',self.char.id))
    def test_proactive_same_request_and_candidates(self):
        _,result=self.run_turn(proactive='关心项目进度')
        self.assertEqual(self.model.calls,1);self.assertTrue(result.attachments)
        self.assertIn('角色主动开启话题',self.model.prompts[0])
    def test_cancellation_after_prefix_discards_pending_and_no_old_images(self):
        ctx=ExpressionContext('g',self.char.id,'desktop:x',early_before=True)
        def callback(event):
            self.events.append(event)
            if event.kind=='attachment_ready':self.token.cancel()
        with self.assertRaises(ReplyCancelled):
            list(self.controller.generate('g',self.char,self.token,'嘿',expression_context=ctx,on_expression_event=callback))
        self.assertEqual(self.events[-1].result.status,'cancelled');self.assertFalse(self.events[-1].result.attachments)
        self.assertEqual(self.controller.expression.delivery(self.events[0].attachment.delivery_id)['status'],'cancelled')
        self.token=CancellationToken();self.events=[]
        self.assertTrue(self.run_turn('next')[1].attachments)
    def test_suppression_does_not_publish_attachment(self):
        self.model.mode='silent'
        text,result=self.run_turn(filter=lambda text,status:'' if '不说' in text else text)
        self.assertEqual(result.status,'suppressed');self.assertFalse(result.attachments)
    def test_old_callers_get_clean_text_and_no_extra_generation(self):
        text=''.join(self.controller.generate('g',self.char,self.token,'嘿'))
        self.assertEqual(text,'好耶！');self.assertEqual(self.model.calls,1)
    def test_cues_and_task_still_pass_to_original_parser(self):
        self.model.mode='task'
        text,result=self.run_turn()
        self.assertIn('<task>写文件</task>',text);self.assertNotIn('<task>',result.text)

    def test_invalid_image_only_is_failure_not_stuck_thinking(self):
        self.model.generate_reply_stream=lambda *args:iter(['<sticker asset_id="st_missing0000" position="before_text"/>'])
        with self.assertRaises(RuntimeError):self.run_turn()
        self.assertEqual(self.events[-1].result.status,'failed')
        self.assertFalse(self.events[-1].result.attachments)

    def test_old_annotation_is_not_repeated_as_assistant_dialogue(self):
        old='好耶！\n[角色已发送表情：庆祝完成]'
        self.db.add_message('assistant',old,self.char.id)
        history,_=self.controller.context.build(self.char)
        self.assertEqual(history[-1].content,'好耶！\n')
        self.assertEqual(self.db.recent_messages(1,self.char.id)[0]['content'],old)

    def test_echo_annotation_is_not_a_delivery_or_visible_text(self):
        self.model.generate_reply_stream=lambda *args:iter('笑死\n[角色已发送表情：开心]')
        text,result=self.run_turn()
        self.assertEqual(text,'笑死\n');self.assertFalse(result.attachments)
        self.assertIn('internal_annotation_echo',result.diagnostics)
        self.assertNotIn('角色已发送表情',self.db.recent_messages(1,self.char.id)[0]['content'])

    def test_receipt_context_obeys_channel_and_deletion_watermark(self):
        self.model.mode='pure';_,result=self.run_turn()
        self.controller.expression.receipt(result.attachments[0].delivery_id,'displayed')
        _,prompt=self.controller.context.build(self.char,channel='qq:group:other')
        self.assertNotIn('历史附件回执',prompt)
        last=self.db.recent_messages(1,self.char.id)[0]['id']
        self.db.set_setting('context_after:'+self.char.id,last)
        history,prompt=self.controller.context.build(self.char)
        self.assertFalse(history);self.assertNotIn('历史附件回执',prompt)

    def test_explicit_text_choice_is_one_request_and_never_in_history(self):
        def model(history,prompt,cancellation):
            self.model.calls+=1
            yield '<expression mode="text_only"/>认真听你说。'
        self.model.generate_reply_stream=model
        text,result=self.run_turn()
        self.assertEqual(text,'认真听你说。');self.assertEqual(self.model.calls,1)
        self.assertFalse(result.attachments)
        self.assertNotIn('<expression',self.db.recent_messages(1,self.char.id)[0]['content'])

    def test_format_examples_are_ephemeral_and_real_context_is_preserved(self):
        self.controller.record_user_message('今天聊点轻松的')
        captured=[]
        def model(history,prompt,cancellation):
            self.model.calls+=1;captured.extend(history)
            yield '<expression mode="text_only"/>聊呀。'
        self.model.generate_reply_stream=model
        self.run_turn()
        self.assertIn('虚构表达格式示例',captured[0].content)
        self.assertEqual(captured[-1].content,'今天聊点轻松的')
        self.assertEqual(self.model.calls,1)
        stored=self.db.recent_messages(30,self.char.id)
        self.assertEqual(len(stored),2)
        self.assertFalse(any('虚构表达格式示例' in r['content'] for r in stored))
