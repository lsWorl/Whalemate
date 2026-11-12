"""桌宠离开时的记忆。只用本机设置和当前角色的桌面聊天记录。"""
from __future__ import annotations

import time

from aicompanion.services.mood_service import MoodService


def remember_departure(db, character_id, reason, scheduler, *, clock=time.time):
    mood = MoodService(db, clock=clock).current(character_id)
    record = dict(reason=reason, mood=mood.label(), levels=mood.levels, at=clock(), pending=True)

    def save(c):
        if not db.setting_in(c, 'self_exit_enabled', True):
            return False
        scheduler.release_on_exit(c)
        c.execute("UPDATE agent_runs SET status='paused' WHERE status='running'")
        db.set_setting_in(c, 'self_exit:'+character_id, record)
        return True

    return db.locked(save)


def take_return_greeting(db, character_id, *, clock=time.time):
    key = 'self_exit:'+character_id
    if not (db.get_setting(key) or {}).get('pending'):
        return ''
    mood = MoodService(db, clock=clock).current(character_id).label()

    def take(c):
        record = db.setting_in(c, key)
        if not record or not record.get('pending'):
            return ''
        endings = {'angry': '我还有一点气，让我慢慢说。', 'sad': '还是有点委屈，你陪我坐坐好吗？',
                   'happy': '现在心情好多啦，又想来找你说话了。', 'shy': '现在有点想你了。'}
        calm = '这会儿缓过来了。' if record['mood'] in ('angry', 'sad') else '这会儿我回来啦。'
        reason = record['reason'].rstrip('。！？!?')
        line = f'上次我离开，是因为{reason}。{endings.get(mood, calm)}'
        # 消费标记和写入聊天一起提交。即使弹气泡前进程意外结束，仍能在桌面聊天找到这一句。
        c.execute("INSERT INTO chat_messages(role,content,character_id,source) VALUES ('assistant',?,?,'self_exit')",
                  (line, character_id))
        db.set_setting_in(c, key, dict(record, pending=False))
        return line

    return db.locked(take)
