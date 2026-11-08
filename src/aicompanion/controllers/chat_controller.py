from __future__ import annotations

from uuid import uuid4
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.services.context_service import ContextService
from aicompanion.services.emotion_service import EmotionService


class ChatController:
    """纯业务流程可由桌面或后续安卓适配器调用，不依赖 Qt。"""

    def __init__(self,database,memory_service,llm_client):
        self.database,self.memory_service,self.llm_client = database,memory_service,llm_client
        self.character = database.characters()[0] if database.characters() else DEFAULT_CHARACTER
        self.context = ContextService(database,memory_service)
        self.emotions = EmotionService(database)
        # 每个角色的表情 / 动作词表（来自角色包）；模型可在台词里标注，存库前去掉
        self.vocabs = {}
        # P5：允许在回复里用 <task> 提议动手（由界面根据设置打开）
        self.agent_enabled = False
        from aicompanion.expression.service import ExpressionService
        self.expression = ExpressionService(database)

    def record_user_message(self,content,source='text',channel='desktop'):
        message_id = self.database.add_message('user',content,self.character.id,source=source,channel=channel)
        self.emotions.update(self.character.id,content)
        self.memory_service.maybe_extract_memory(content,self.character.id,message_id)
        return message_id

    def generate(self,turn_id,character,cancellation,query='',proactive='',channel='desktop',scene='pet',situation='',source='text',offer_tasks=True,purpose='companion',through_id=None,reply_filter=None,*,expression_context=None,on_expression_event=None):
        """所有自然聊天共用这一轮生成；图片控制在 yield 给 UI/TTS 之前剥离。"""
        from dataclasses import replace
        from aicompanion.expression.domain import ExpressionContext,ExpressionEvent
        from aicompanion.expression.stream import ExpressionStream
        from aicompanion.pet.script import Vocab,strip_cues
        context=expression_context or ExpressionContext(turn_id,character.id,'desktop:'+character.id,
            channel,scene,'proactive' if proactive else 'user_reply',proactive,supports_images=bool(on_expression_event))
        if context.generation_id!=turn_id or context.character_id!=character.id:
            raise ValueError('表达上下文与当前轮次不一致')
        context=replace(context,supports_images=context.supports_images and on_expression_event is not None,
                        early_before=context.early_before and reply_filter is None)
        self.database.start_turn(turn_id,character.id)
        parts=[];status='complete';error='';attachment=None;parser=None;candidates=()
        try:
            history,prompt=self.context.build(character,query,channel,scene,situation,through_id)
            if proactive:
                prompt+='\n现在是角色主动开启话题：'+proactive+'。只发一句自然问候或追问，不编造经历。'
            vocab=self.vocabs.get(character.id)
            if vocab:prompt+=vocab.prompt()
            if self.agent_enabled and not proactive and offer_tasks:
                from aicompanion.pet.script import TASK_PROMPT
                prompt+=TASK_PROMPT
            candidates=self.expression.prepare(context,character,query,history)
            prompt+=self.expression.prompt(candidates)
            if context.more_memes:prompt+='\n对方偏好合适时用表情交流，但不要求每轮发图，仍由你自己决定。'
            parser=ExpressionStream([c.asset_id for c in candidates],self.expression.legacy_map(candidates))
            choices={c.asset_id:c for c in candidates}
            from aicompanion.usage.meter import using
            # 格式示例只进入这一轮请求，不写聊天 / 记忆；候选检索仍只看真实上下文。
            model_history=self.expression.format_examples(candidates)+history
            with using(purpose,character.id,turn_id):
                for token in self.llm_client.generate_reply_stream(model_history,prompt,cancellation):
                    cancellation.check()
                    clean=parser.feed(token)
                    if context.early_before and attachment is None and parser.selection and parser.selection[1]=='before_text':
                        attachment=self.expression.attachment(turn_id,context,choices[parser.selection[0]],'before_text')
                        cancellation.check()
                        if attachment:on_expression_event(ExpressionEvent('attachment_ready',turn_id,attachment))
                        cancellation.check()
                    if clean:parts.append(clean);yield clean
            cancellation.check()
            tail=parser.finish()
            if tail:parts.append(tail);yield tail
            cancellation.check()
            if attachment is None and parser.selection:
                aid,placement=parser.selection
                attachment=self.expression.attachment(turn_id,context,choices[aid],placement)
            if not ''.join(parts).strip() and attachment is None:
                raise RuntimeError('模型未返回有效内容。')
        except ReplyCancelled:
            status='cancelled'
            if parser:parser.cancel()
            raise
        except GeneratorExit:
            status='cancelled';cancellation.cancel()
            if parser:parser.cancel()
            raise
        except Exception as exc:
            status,error='failed',str(exc)
            raise
        finally:
            content=strip_cues(''.join(parts),self.vocabs.get(character.id) or Vocab())
            if reply_filter is not None:
                try:
                    filtered=reply_filter(content,status)
                    if content.strip() and not filtered.strip():status='suppressed' if status=='complete' else status
                    content=filtered
                except Exception:
                    status='failed';error='回复过滤失败'
            if attachment and self.expression.catalog.resolve(attachment.asset_id,attachment.revision) is None:
                self.expression.receipt(attachment.delivery_id,'cancelled');attachment=None
            result=self.expression.result(turn_id,context,content,attachment,status,parser.diagnostics if parser else ())
            self.database.finish_turn(turn_id,character.id,content,status,error,channel=channel,source=source)
            if on_expression_event:on_expression_event(ExpressionEvent('final',turn_id,result=result))

    def stream_user_message(self,content):
        self.record_user_message(content)
        yield from self.generate(str(uuid4()),self.character,CancellationToken(),content)
