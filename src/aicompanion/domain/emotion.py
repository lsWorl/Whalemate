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

