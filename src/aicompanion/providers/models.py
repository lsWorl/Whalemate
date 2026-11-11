"""模型配置的数据结构与内置预设。

三类持久配置（计划书 v4 功能一）：
- ProviderProfile：一家服务的一个账户（地址、地区、计费方式、凭据引用）；
- ModelProfile：这个账户下的一个具体模型（model_id、能力、按用途的思考策略与输出额度）；
- Binding：某种能力（chat / vision / search / agent）用哪个模型，可以按角色单独指定。

其余都是值对象：Resolved 是一次请求的冻结快照，后台线程拿到后不再读全局状态。
预设只是“可更新的数据”，业务代码不按模型名字做判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 能力：聊天、识图、搜索、办事，以及语音合成（tts，音色按角色设置）。语音识别在后续阶段加入。
CAPABILITIES = ('chat', 'vision', 'search', 'agent', 'tts')

# 用途：同一个模型在不同用途下用不同的思考策略和输出额度。
# 聊天追求快；识图、表情分析、后台 JSON 分析要保证正文不被推理吃光。
PURPOSE_KIND = {
    'companion': 'chat', 'qq': 'chat', 'voice': 'chat', 'proactive': 'chat', 'purpose': 'analysis',
    'memory': 'analysis', 'relationship': 'analysis', 'community': 'analysis', 'translation': 'analysis',
    'vision': 'vision', 'avatar': 'vision', 'sticker': 'vision',
}

# 思考策略：off 能关就关；default 不发送任何参数，交给服务端；on 显式开启。
THINKING_POLICIES = ('off', 'default', 'on')

# 供应商种类决定“思考策略”怎么翻译成请求参数。新种类只需在 router.thinking_params 里加一段。
PROVIDER_KINDS = ('openai_compatible', 'deepseek', 'minimax', 'breezeblue')


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    kind: str = 'openai_compatible'
    base_url: str = ''
    region: str = ''                 # global / cn / custom；MiniMax 国际站与国内站的账户和 Key 互不通用
    billing: str = 'payg'            # payg 按量 / plan 订阅 / custom；订阅 Key 与按量 Key 不通用
    credential: str = ''             # 凭据引用，例如 env:LLM_API_KEY 或 stored:deepseek-main；不是明文
    revision: int = 1

    @staticmethod
    def from_dict(data: dict) -> 'ProviderProfile':
        return ProviderProfile(**{k: data[k] for k in ProviderProfile.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider: str                    # ProviderProfile.id
    model_id: str
    label: str = ''
    capabilities: tuple = ('chat',)  # chat / vision / tools
    thinking: dict = field(default_factory=dict)      # {用途种类: off/default/on}，缺省按 DEFAULT_THINKING
    can_disable_thinking: bool = True
    max_output: dict = field(default_factory=dict)    # {用途种类: 输出上限}；None 表示不发送
    temperature: float | None = None                  # None 表示沿用全局设置
    context_window: int | None = None                 # 未知就留空，不猜
    reasoning_in_output: bool = True                  # 推理 Token 是否占用输出额度（两家官方文档都写明占用）
    revision: int = 1

    @staticmethod
    def from_dict(data: dict) -> 'ModelProfile':
        values = {k: data[k] for k in ModelProfile.__dataclass_fields__ if k in data}
        if 'capabilities' in values: values['capabilities'] = tuple(values['capabilities'])
        return ModelProfile(**values)


@dataclass(frozen=True)
class Binding:
    capability: str
    model: str = ''                  # ModelProfile.id；search 用 backend
    character_id: str | None = None  # None = 全局默认
    backend: str = ''                # search：deepseek / wikipedia
    provider: str = ''               # search：用哪个账户的凭据
    route: str = ''                  # agent：Harness 里的路由，例如 deepseek-official / minimax
    voice: str = ''                  # tts：音色 ID（例如 Breeze Blue 控制台里的 voc_xxx）
    language: str = ''               # tts：朗读语言代码（zh / en / ja ...），留空交给模型判断
    speed: float = 1.0               # tts：语速
    volume: float = 1.0              # tts：音量倍数
    instructions: str = ''           # tts：固定的语气说明，和当前情绪说明拼在一起发送

    @staticmethod
    def from_dict(data: dict) -> 'Binding':
        return Binding(**{k: data[k] for k in Binding.__dataclass_fields__ if k in data})


@dataclass(frozen=True)
class Resolved:
    """一次请求的配置快照。拿到之后就不再随设置变化，当前这一轮用它说完。"""
    capability: str
    character_id: str
    purpose: str
    source: str                      # character / global / pack / legacy：这份配置从哪一层来，界面上显示给用户
    provider_id: str
    provider_kind: str
    base_url: str
    model_id: str
    credential: str
    credential_revision: str
    config_revision: int
    label: str
    temperature: float
    timeout_seconds: int
    max_output: int | None = None
    extra: tuple = ()                # 供应商专用参数（已按思考策略翻译好）
    note: str = ''                   # 给界面看的补充说明，例如“未配置专属密钥，暂用默认模型”

    @property
    def cache_key(self) -> tuple:
        """客户端缓存键：地址、模型、凭据修订、参数任何一项变了都换新实例。"""
        return (self.provider_kind, self.base_url, self.model_id, self.credential, self.credential_revision,
                self.temperature, self.timeout_seconds, self.max_output, self.extra)


# 用途种类的默认思考策略：聊天要快，识图和分析要稳。
DEFAULT_THINKING = {'chat': 'off', 'analysis': 'off', 'vision': 'off'}
# 必须思考的模型给识图和分析留足额度，避免推理把正文挤掉（官方：推理计入输出上限）。
DEFAULT_MAX_OUTPUT_WHEN_THINKING = {'vision': 4000, 'analysis': 4000}


# ---- 内置预设（资料核对日期 2026-11-10；只作为可编辑的初始数据） ----------------------------
PROVIDER_PRESETS = {
    'deepseek': ProviderProfile('deepseek', 'DeepSeek', 'deepseek', 'https://api.deepseek.com', 'global', 'payg'),
    'minimax-global': ProviderProfile('minimax-global', 'MiniMax 国际站', 'minimax', 'https://api.minimax.io/v1', 'global', 'payg'),
    # 国内站地址各文档写法不一（api.minimaxi.com / api.minimax.cn），以用户账户页面为准，可在设置里修改。
    'minimax-cn': ProviderProfile('minimax-cn', 'MiniMax 国内站', 'minimax', 'https://api.minimaxi.com/v1', 'cn', 'payg'),
    # Breeze Blue 开发者 API（ElevenLabs 形态，鉴权头 xi-api-key）。资料核对 2026-11-11：docs.breezeblue.ai
    'breezeblue': ProviderProfile('breezeblue', 'Breeze Blue', 'breezeblue', 'https://api.breeze.blue/v1', 'global', 'payg'),
}

MODEL_PRESETS = {
    'deepseek-flash': ModelProfile('deepseek-flash', 'deepseek', 'deepseek-flash', 'DeepSeek Flash',
                                   ('chat', 'vision', 'tools')),
    'deepseek-v4-pro': ModelProfile('deepseek-v4-pro', 'deepseek', 'deepseek-v4-pro', 'DeepSeek V4 Pro', ('chat', 'tools')),
    'minimax-m3.1-flash': ModelProfile('minimax-m3.1-flash', 'minimax-global', 'MiniMax-M3.1-Flash', 'MiniMax M3.1 Flash',
                                       ('chat', 'vision', 'tools'), can_disable_thinking=False, context_window=1_000_000),
    'minimax-m3': ModelProfile('minimax-m3', 'minimax-global', 'MiniMax-M3', 'MiniMax M3',
                               ('chat', 'vision', 'tools'), context_window=1_000_000),
    # 中文、英文用 breeze-tts-2；其他语言（含日语）用多语言版
    'breeze-tts-2': ModelProfile('breeze-tts-2', 'breezeblue', 'breeze-tts-2', 'Breeze TTS 2', ('tts',)),
    'breeze-tts-2-multilingual': ModelProfile('breeze-tts-2-multilingual', 'breezeblue', 'breeze-tts-2-multilingual',
                                              'Breeze TTS 2 多语言', ('tts',)),
}


def kind_for_url(base_url: str) -> str:
    """旧配置没有写供应商种类时，按官方地址推断；只用于旧配置，新配置由用户选择。"""
    from urllib.parse import urlsplit
    host = (urlsplit(base_url or '').hostname or '').lower()
    if host == 'api.deepseek.com': return 'deepseek'
    if host.endswith('minimax.io') or host.endswith('minimaxi.com') or host.endswith('minimax.cn'): return 'minimax'
    if host.endswith('breeze.blue'): return 'breezeblue'
    return 'openai_compatible'
