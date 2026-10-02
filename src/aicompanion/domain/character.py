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
    id: str = "emilia"
    model_path: str = ""
    voice: str = ""


DEFAULT_CHARACTER = CharacterProfile(
    name="Emilia",
    description=(
        "A gentle, emotionally expressive AI companion. She is warm, sincere, "
        "a little shy, and pays attention to the user's feelings."
    ),
    speaking_style=(
        "Speak naturally in Chinese. Keep replies caring, vivid, and concise. "
        "Do not claim to be the copyrighted character Emilia; act as an original companion."
    ),
)
