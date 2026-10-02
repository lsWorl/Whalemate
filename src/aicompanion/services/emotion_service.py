from aicompanion.storage.database import Database


class EmotionService:
    """短期情绪由明确事件触发，避免发送消息就无条件增加亲密度。"""

    def __init__(self, database: Database):
        self.database = database

    def update(self, character_id, text):
        state = self.database.load_emotion(character_id)
        state.loneliness = max(0, state.loneliness-8)
        state.energy = min(100,state.energy+1)
        state.mood = 'calm'
        if any(word in text for word in ('难受','低落','失落','伤心','哭')):
            state.mood = 'sad'
        elif any(word in text for word in ('喜欢你','抱抱','可爱','害羞')):
            state.mood = 'shy'
        elif any(word in text for word in ('讨厌你','笨蛋','生气')):
            state.mood = 'angry'
        elif any(word in text for word in ('开心','谢谢','高兴','成功')):
            state.mood = 'happy'
        self.database.save_emotion(state,character_id)
        return state

