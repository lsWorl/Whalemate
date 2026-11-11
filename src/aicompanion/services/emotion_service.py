from aicompanion.storage.database import Database


class EmotionService:
    """短期情绪由明确事件触发，避免发送消息就无条件增加亲密度。

    计划书 v4 功能三起，心情不再每句话重置：交给 MoodService 按事件推动、随时间回落（见 domain/mood.py）。
    这里只保留孤独感、精力这两个旧数值，以及把用户消息交给 MoodService 做轻量兜底。"""

    def __init__(self, database: Database, clock=None):
        import time
        self.database = database; self.clock = clock or time.time

    def update(self, character_id, text, channel='desktop'):
        from aicompanion.services.mood_service import MoodService
        state = self.database.load_emotion(character_id)
        state.loneliness = max(0, state.loneliness-8)
        state.energy = min(100,state.energy+1)
        self.database.save_emotion(state,character_id)
        key = f'mood_last_seen:{character_id}'
        previous = self.database.get_setting(key) if channel == 'desktop' else None
        now = self.clock()
        if channel == 'desktop': self.database.set_setting(key, now)
        try:
            MoodService(self.database, clock=self.clock).on_user_message(character_id, text, channel, previous)
        except Exception:
            pass   # 心情出错不影响聊天
        if any(word in text for word in ('开心','谢谢','高兴','成功')):
            self.database.record_relationship_event(character_id)
        return self.database.load_emotion(character_id)

