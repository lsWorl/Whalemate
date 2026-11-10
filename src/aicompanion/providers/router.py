"""按“角色 + 能力 + 用途”解析出这一次请求用哪个服务、哪个模型、哪把 Key。

查找顺序（计划书 v4 功能一）：用户给这个角色单独指定的 → 用户的全局设置。没有别的来源：
不读 .env 的 LLM_*，也不读角色包里的模型推荐。

- 聊天没配置：用离线模拟模型，界面提示去设置里配置；
- 识图没配置：不看图，消息里只留 [图片]；
- 搜索没配置：用维基百科（不需要 Key）；
- 办事没配置：不启用办事。

解析结果是冻结的快照（Resolved），后台线程拿着它把这一轮说完；换 Key、换模型只影响下一次请求。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.models import (DEFAULT_MAX_OUTPUT_WHEN_THINKING, DEFAULT_THINKING, PURPOSE_KIND,
                                          ModelProfile, ProviderProfile, Resolved, kind_for_url)
from aicompanion.providers.store import ModelConfigStore

# Harness 路由 → 它读取的 Key 环境变量与地址变量（锁定的 DSH 0.2.1rc2 内置；新路由验证后再加）
AGENT_ROUTES = {
    'deepseek-official': ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL'),
    'minimax': ('MINIMAX_API_KEY', None),
    'minimax-cn': ('MINIMAX_CN_API_KEY', None),
}
OFFLINE_LABEL = '离线模拟模型'


class ModelUnavailable(RuntimeError):
    """没配置或现在用不了（例如 Key 没填）。不偷偷换到别的收费服务，交给界面提示。"""


@dataclass(frozen=True)
class SearchTarget:
    backend: str          # deepseek / wikipedia
    base_url: str = ''
    api_key: str = ''


@dataclass(frozen=True)
class TtsTarget:
    """语音合成的冻结配置。cache_id 不含 Key，换音色、模型、参数或 Key 都会变，语音缓存据此区分。"""
    provider_kind: str
    base_url: str
    api_key: str = field(repr=False)
    model_id: str
    voice: str
    language: str
    speed: float
    volume: float
    instructions: str
    cache_id: str
    label: str


@dataclass(frozen=True)
class AgentTarget:
    route: str
    model_id: str
    env: tuple            # ((变量名, 值), ...)，只放本次选定的凭据
    label: str


def thinking_params(kind: str, model: ModelProfile, purpose: str) -> tuple[dict, int | None]:
    """把“按用途的思考策略”翻译成供应商的请求参数，并给出输出上限。

    官方文档（2026-11-10 核对）：DeepSeek 默认开启思考，可用 thinking.type=disabled 关闭；
    MiniMax 用 reasoning_split 把推理放进独立字段，M3.1-Flash 不能关闭思考；两家的推理都占用输出额度。
    """
    group = PURPOSE_KIND.get(purpose, 'chat')
    policy = model.thinking.get(group) or DEFAULT_THINKING.get(group, 'default')
    extra, thinks = {}, policy != 'off'
    if kind == 'deepseek':
        if policy == 'off': extra['thinking'] = {'type': 'disabled'}
        elif policy == 'on': extra['thinking'] = {'type': 'enabled'}
    elif kind == 'minimax':
        extra['reasoning_split'] = True          # 推理不混进正文，避免进气泡和语音
        if policy == 'off' and model.can_disable_thinking: extra['thinking'] = {'type': 'disabled'}
        else: thinks = True
    else:
        thinks = policy == 'on'                  # 兼容服务不发送任何专用参数
    limit = model.max_output.get(group)
    if limit is None and thinks and model.reasoning_in_output:
        limit = DEFAULT_MAX_OUTPUT_WHEN_THINKING.get(group)
    return extra, limit


def thaw(value):
    """Resolved.extra 是冻结的元组，发请求前还原成字典。"""
    if isinstance(value, tuple) and all(isinstance(i, tuple) and len(i) == 2 for i in value):
        return {k: thaw(v) for k, v in value}
    return value


def _freeze(value):
    return tuple(sorted((k, _freeze(v)) for k, v in value.items())) if isinstance(value, dict) else value


class ModelRouter:
    TEMPERATURE = 0.8
    TIMEOUT = 60

    def __init__(self, database, settings=None, credentials=None, client_factory=None):
        self.db, self.settings = database, settings
        self.credentials = credentials or CredentialStore(database)
        self.store = ModelConfigStore(database)
        self._factory = client_factory
        self._clients = {}
        self._mock = None
        self._lock = threading.Lock()

    @property
    def offline(self) -> bool:
        return bool(getattr(self.settings, 'offline', False))

    # ---- 解析 --------------------------------------------------------------------------------
    def configured(self, capability: str, character_id: str) -> bool:
        config = self.store.load()
        return any(b and b.model in config.models and config.models[b.model].provider in config.providers
                   for b in (config.binding(capability, character_id), config.binding(capability, None)))

    def ready(self, capability: str, character_id: str) -> bool:
        """能真的发请求：在线、配置了、Key 也在。离线或没配好时，后台分析等功能直接跳过。"""
        if self.offline: return False
        try:
            return bool(self.secret(self.resolve(capability, character_id)))
        except ModelUnavailable:
            return False

    def resolve(self, capability: str, character_id: str, purpose: str = 'companion') -> Resolved:
        if capability not in ('chat', 'vision'):
            raise ValueError(f'resolve 不处理 {capability}，请用 search_target / agent_target')
        config = self.store.load()
        for source, binding in (('character', config.binding(capability, character_id)),
                                ('global', config.binding(capability, None))):
            if binding and binding.model in config.models:
                model = config.models[binding.model]
                provider = config.providers.get(model.provider)
                if provider:
                    return self._make(capability, character_id, purpose, source, provider, model, config.revision)
        name = '聊天' if capability == 'chat' else '识图'
        raise ModelUnavailable(f'还没有配置{name}模型，请到设置的“模型”页选择。')

    def search_target(self, character_id: str) -> SearchTarget:
        config = self.store.load()
        for binding in (config.binding('search', character_id), config.binding('search', None)):
            if not binding: continue
            if binding.backend == 'deepseek' and binding.provider in config.providers:
                provider = config.providers[binding.provider]
                return SearchTarget('deepseek', provider.base_url, self.credentials.get(provider.credential))
            if binding.backend == 'wikipedia':
                return SearchTarget('wikipedia')
        return SearchTarget('wikipedia')

    def tts_target(self, character_id: str) -> TtsTarget | None:
        """这个角色的语音合成配置（音色可以按角色单独设置）。没配置或没有 Key 返回 None。"""
        if self.offline: return None
        config = self.store.load()
        for binding in (config.binding('tts', character_id), config.binding('tts', None)):
            if not (binding and binding.model in config.models and binding.voice): continue
            model = config.models[binding.model]
            provider = config.providers.get(model.provider)
            if not provider: continue
            key = self.credentials.get(provider.credential)
            if not key: return None
            revision = self.credentials.revision(provider.credential)
            cache_id = '|'.join((provider.kind, provider.base_url, model.model_id, binding.voice, binding.language,
                                 f'{binding.speed:g}', f'{binding.volume:g}', binding.instructions, revision))
            return TtsTarget(provider.kind, provider.base_url.rstrip('/'), key, model.model_id, binding.voice, binding.language,
                             binding.speed, binding.volume, binding.instructions, cache_id,
                             f'{provider.label} · {model.model_id} · {binding.voice}')
        return None

    def agent_target(self, character_id: str | None = None) -> AgentTarget | None:
        """办事用的 Harness 路由、模型和只含选定凭据的环境变量。没配置或没有 Key 返回 None。"""
        if self.offline: return None
        config = self.store.load()
        for binding in (config.binding('agent', character_id) if character_id else None, config.binding('agent', None)):
            if binding and binding.model in config.models and binding.route in AGENT_ROUTES:
                model = config.models[binding.model]
                provider = config.providers.get(model.provider)
                if not provider: continue
                key = self.credentials.get(provider.credential)
                if not key: return None
                key_env, base_env = AGENT_ROUTES[binding.route]
                env = [(key_env, key)]
                if base_env and provider.base_url and provider.base_url.rstrip('/') != 'https://api.deepseek.com':
                    env.append((base_env, provider.base_url))
                return AgentTarget(binding.route, model.model_id, tuple(env), f'{provider.label} · {model.model_id}')
        return None

    # ---- 客户端 ------------------------------------------------------------------------------
    def client(self, capability: str, character_id: str, purpose: str = 'companion'):
        """返回 (客户端, 快照)。聊天没配置或处于离线模式时返回离线模拟模型，快照为 None。"""
        if self.offline or (capability == 'chat' and not self.configured('chat', character_id)):
            return self.mock(), None
        resolved = self.resolve(capability, character_id, purpose)
        return self.client_for(resolved), resolved

    def mock(self):
        if self._mock is None:
            from aicompanion.llm.mock_client import MockLLMClient
            self._mock = MockLLMClient()
        return self._mock

    def client_for(self, resolved: Resolved):
        """同样的地址、模型、凭据修订和参数复用同一个实例；任何一项变了就换新的。"""
        key = resolved.cache_key
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                secret = self.credentials.get(resolved.credential)
                if not secret:
                    raise ModelUnavailable(f'{resolved.label} 的 Key 还没有设置或已失效，请到设置里填写。')
                client = (self._factory or openai_client)(resolved, secret)
                # 只保留每个“地址 + 模型”的最新凭据实例，旧凭据的实例随之丢弃
                for k in [k for k in self._clients if k[:3] == key[:3] and k[4] != key[4]]:
                    self._clients.pop(k, None)
                self._clients[key] = client
            return client

    def secret(self, resolved: Resolved) -> str:
        """构造请求时取 Key；不要保存或写进日志。"""
        return self.credentials.get(resolved.credential)

    def label(self, character_id: str) -> str:
        if self.offline: return OFFLINE_LABEL
        try:
            return self.resolve('chat', character_id).label
        except ModelUnavailable:
            return '未配置模型（离线模拟）'

    # ---- 内部 ------------------------------------------------------------------------------
    def _make(self, capability, character_id, purpose, source, provider: ProviderProfile, model: ModelProfile, revision) -> Resolved:
        extra, limit = thinking_params(provider.kind, model, purpose)
        temperature = model.temperature if model.temperature is not None else (0.0 if capability == 'vision' else self.TEMPERATURE)
        return Resolved(capability, character_id, purpose, source, provider.id, provider.kind, provider.base_url.rstrip('/'),
                        model.model_id, provider.credential, self.credentials.revision(provider.credential), revision,
                        f'{provider.label} · {model.model_id}', temperature, self.TIMEOUT,
                        limit, tuple(sorted((k, _freeze(v)) for k, v in extra.items())))


def openai_client(resolved: Resolved, secret: str):
    from aicompanion.llm.openai_compatible_client import OpenAICompatibleClient
    return OpenAICompatibleClient(base_url=resolved.base_url, api_key=secret, model=resolved.model_id,
                                  temperature=resolved.temperature, timeout_seconds=resolved.timeout_seconds,
                                  extra=thaw(resolved.extra), max_tokens=resolved.max_output)


class FixedRouter:
    """只有一个客户端的解析器：测试、诊断脚本和桌宠自检用，接口与 ModelRouter 相同。

    vision_model 给出时识图用同一地址和 Key 的这个模型；search 默认维基百科。
    """
    offline = False

    def __init__(self, database, client, vision_model: str | None = None, search: SearchTarget | None = None):
        self.db, self.fixed = database, client
        self.vision_model = vision_model
        self.search = search or SearchTarget('wikipedia')

    def configured(self, capability, character_id): return capability == 'chat' or (capability == 'vision' and bool(self.vision_model))

    def ready(self, capability, character_id): return self.configured(capability, character_id)

    def resolve(self, capability: str, character_id: str, purpose: str = 'companion') -> Resolved:
        c = self.fixed
        base = str(getattr(c, 'base_url', '') or '').rstrip('/')
        model, temperature = str(getattr(c, 'model', '') or ''), float(getattr(c, 'temperature', 0.8) or 0.8)
        if capability == 'vision':
            if not self.vision_model: raise ModelUnavailable('还没有配置识图模型')
            model, temperature = self.vision_model, 0.0
        elif capability != 'chat':
            raise ValueError(f'resolve 不处理 {capability}')
        return Resolved(capability, character_id, purpose, 'fixed', 'fixed', kind_for_url(base), base, model, 'fixed', '', 0,
                        f'固定模型 · {model}' if model else '固定模型', temperature, int(getattr(c, 'timeout_seconds', 60) or 60))

    def client(self, capability, character_id, purpose='companion'):
        return self.fixed, self.resolve(capability, character_id, purpose)

    def client_for(self, resolved): return self.fixed

    def secret(self, resolved) -> str: return str(getattr(self.fixed, 'api_key', '') or '')

    def search_target(self, character_id) -> SearchTarget: return self.search

    def agent_target(self, character_id=None): return None

    def label(self, character_id) -> str: return self.resolve('chat', character_id).label
