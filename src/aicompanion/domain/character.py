from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CharacterProfile:
    """角色档案。

    这里先保存最核心的角色信息。后续可以扩展：
    - 头像/Live2D 模型路径
    - 不同情绪对应的表情名
    - 角色常用口癖
    - 不同亲密度阶段的说话方式
    """

    name: str
    description: str
    speaking_style: str
    id: str = "deepseek-whale"
    model_path: str = ""
    voice: str = ""


# 数据库初始化时写入的占位角色：与鲸娘角色包同一个 ID，角色包同步时用包里的人设替换这段占位文字。
DEFAULT_CHARACTER = CharacterProfile(
    name="大肥鱼",
    description="桌面上的 AI 伙伴。（角色包尚未同步时的占位设定）",
    speaking_style="用自然的中文聊天，简短、真诚。",
)
