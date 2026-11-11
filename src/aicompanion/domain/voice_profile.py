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


def adopt_default_voice(database, router, character_id: str, env_provider: str = 'system') -> bool:
    """首次自动接入角色默认声线（Codex PR #41 审查 2、1）。只在下面几条都满足时把朗读方式切到云端声线，且只做一次：

    - 数据库里从没存过朗读方式（None）。存过的——包括明确选的“本地朗读（不计费、文本不上传）”——一律不动；
      旧库里存着 system 的无法区分是默认还是用户选的，按用户选的保留，要用新声线由用户自己去设置里切。
    - 环境配置没有指定别的朗读方式（TTS_PROVIDER 不是默认的 system 时尊重它）。
    - 当前角色确实有可用的默认声线（例如已经有 MiniMax 账户）。
    - router 没有默认声线能力（FixedRouter 等测试 / 诊断 / 预览用的解析器）时安全跳过，不报错。
    返回是否做了切换。"""
    if database.get_setting('voice_profile_setup') is not None or database.get_setting('tts_provider') is not None:
        return False
    if (env_provider or 'system') != 'system': return False
    default_voice = getattr(router, 'default_voice', None)
    if default_voice is None or not default_voice(character_id): return False
    database.set_setting('tts_provider', 'breeze'); database.set_setting('voice_profile_setup', character_id)
    return True
