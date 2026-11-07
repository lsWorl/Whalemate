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

    def record_user_message(self,content,source='text',channel='desktop'):
        message_id = self.database.add_message('user',content,self.character.id,source=source,channel=channel)
        self.emotions.update(self.character.id,content)
        self.memory_service.maybe_extract_memory(content,self.character.id,message_id)
        return message_id

    def generate(self,turn_id,character,cancellation,query='',proactive='',channel='desktop',scene='pet',situation='',source='text',offer_tasks=True,purpose='companion',through_id=None):
        """channel / scene / situation 见 ContextService.build；聊天平台进来的消息 offer_tasks=False（不在聊天平台上提议动手）。"""
        self.database.start_turn(turn_id,character.id)
        parts,status,error = [],'complete',''
        try:
            history,prompt = self.context.build(character,query,channel,scene,situation,through_id)
            if proactive:
                prompt += '\n现在是角色主动开启话题：'+proactive+'。只发一句自然问候或追问，不编造经历。'
            vocab = self.vocabs.get(character.id)
            if vocab: prompt += vocab.prompt()
            if self.agent_enabled and not proactive and offer_tasks:
                from aicompanion.pet.script import TASK_PROMPT
                prompt += TASK_PROMPT
            from aicompanion.usage.meter import using
            with using(purpose,character.id,turn_id):
                for token in self.llm_client.generate_reply_stream(history,prompt,cancellation):
                    cancellation.check()
                    parts.append(token)
                    yield token
            cancellation.check()
            if not ''.join(parts).strip():
                raise RuntimeError('模型未返回有效内容。')
        except ReplyCancelled:
            status = 'cancelled'
            raise
        except Exception as exc:
            status,error = 'failed',str(exc)
            raise
        finally:
            content = ''.join(parts)
            vocab = self.vocabs.get(character.id)
            from aicompanion.pet.script import Vocab, strip_cues
            content = strip_cues(content, vocab or Vocab())
            self.database.finish_turn(turn_id,character.id,content,status,error,channel=channel,source=source)

    def stream_user_message(self,content):
        self.record_user_message(content)
        yield from self.generate(str(uuid4()),self.character,CancellationToken(),content)

