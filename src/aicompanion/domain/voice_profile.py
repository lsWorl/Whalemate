"""角色声线设定（计划书 v4 功能八“大肥鱼声线”的第一步）：声线属于角色，不属于某个语音服务。

负责人 2026-11-11 16:05："希望鲸鲸的默认声音就是网上 AI 视频的那种声线，需要富有感情"。
网上流行的鲸娘 / 大肥鱼声音多是网友用参考音频训练的少女音（来源和授权不明，不能直接拿来用）；我们按同一种感觉
在正规服务的音色库里选：少女感、软糯、带一点慵懒和小傲娇。

- 默认用 MiniMax 语音（和聊天共用一个 MiniMax 账户，不用另配 Key）：中文系统音色里挑了几个最接近的，
  默认“软糯少女”，其余作为候选在模型页试听替换（资料核对 2026-11-11：platform.minimax.io/docs/faq/system-voice-id）。
- 感情：MiniMax 默认按台词内容自动选最自然的情绪；她心情明显（开心、委屈、生气）时再显式指定情绪（EMOTIONS）。
- Breeze Blue 没有现成音色时，可以用 description 走音色设计（以后在模型页加按钮）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VoiceProfile:
    description: str
    minimax_voice: str
    minimax_model: str = 'speech-2.8-hd'          # HD 更有感情；首次出声比 turbo 慢一点
    minimax_candidates: tuple = ()
    speed: float = 1.0
    extra: dict = field(default_factory=dict)


WHALE = VoiceProfile(
    description='年轻少女音，软糯清亮，带一点慵懒和小傲娇，说话有情绪起伏，常带“嘿嘿”“唔……”这类语气词，自称本鲸。',
    minimax_voice='Chinese (Mandarin)_Soft_Girl',
    minimax_candidates=(
        ('Chinese (Mandarin)_Soft_Girl', '软糯少女（默认）'),
        ('Chinese (Mandarin)_Cute_Spirit', '可爱精灵'),
        ('Chinese (Mandarin)_Laid_BackGirl', '慵懒少女'),
        ('Arrogant_Miss', '傲娇小姐'),
        ('Chinese (Mandarin)_BashfulGirl', '害羞少女'),
        ('Chinese (Mandarin)_Crisp_Girl', '清脆少女'),
    ),
)

PROFILES = {'deepseek-whale': WHALE}

# 心情 → MiniMax 情绪。平静、撒娇不指定（交给模型按台词自动选，通常更自然）
EMOTIONS = {'happy': 'happy', 'sad': 'sad', 'angry': 'angry'}


def profile_for(character_id: str) -> VoiceProfile | None:
    return PROFILES.get(character_id)
