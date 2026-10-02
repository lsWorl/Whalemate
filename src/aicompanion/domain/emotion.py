from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmotionState:
    """角色当前情绪状态。

    这些数值暂时用简单规则更新：
    - affection：亲密度
    - loneliness：孤独感，用户久未回复时上升
    - energy：精力，后续可以影响回复长短和主动频率
    - mood：当前心情，可用于驱动表情、语气、Live2D 动作
    """

    mood: str = "calm"
    affection: int = 50
    loneliness: int = 15
    energy: int = 80

    def after_user_message(self) -> None:
        """用户发送消息后，降低孤独感，略微增加亲密度。"""

        self.loneliness = max(0, self.loneliness - 10)
        self.affection = min(100, self.affection + 1)
        self.energy = max(0, self.energy - 1)
        self.mood = "happy" if self.affection >= 55 else "calm"

    def after_idle_tick(self) -> None:
        """用户长时间没回复时，提升孤独感并可能切换到 lonely。"""

        self.loneliness = min(100, self.loneliness + 5)
        if self.loneliness >= 60:
            self.mood = "lonely"
