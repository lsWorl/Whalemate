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

    def load_recent_history(self):
        return self.context.build(self.character)[0]

    def record_user_message(self,content,source='text'):
        message_id = self.database.add_message('user',content,self.character.id,source=source)
        self.emotions.update(self.character.id,content)
        self.memory_service.maybe_extract_memory(content,self.character.id,message_id)
        return message_id

    def generate(self,turn_id,character,cancellation,query='',proactive=''):
        self.database.start_turn(turn_id,character.id)
        parts,status,error = [],'complete',''
        try:
            history,prompt = self.context.build(character,query)
            if proactive:
                prompt += '\n现在是角色主动开启话题：'+proactive+'。只发一句自然问候或追问，不编造经历。'
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
            self.database.finish_turn(turn_id,character.id,''.join(parts),status,error)

    def stream_user_message(self,content):
        self.record_user_message(content)
        yield from self.generate(str(uuid4()),self.character,CancellationToken(),content)

    def send_user_message(self,content):
        return ''.join(self.stream_user_message(content))

