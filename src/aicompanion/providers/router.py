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
from urllib.parse import urlsplit

# Harness 路由 → 它读取的 Key 环境变量与地址变量（锁定的 DSH 0.2.1rc2 内置；新路由验证后再加）
AGENT_ROUTES = {
    'deepseek-official': ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL'),
    'minimax': ('MINIMAX_API_KEY', None),
    'minimax-cn': ('MINIMAX_CN_API_KEY', None),
}
# 各路由能办事的模型（锁定的 DSH 0.2.1rc2 实测）。
# MiniMax 走 Harness 的 llm-pi-ai 多供应商路由（Anthropic 兼容接口），由 dsh_backend 在补丁里按本次选定的路由激活；
# 内置目录有 M3 / M2.7 / M2.7-highspeed，MiniMax-M3.1-Flash 不在目录里，补丁里显式声明（dsh_backend.MODEL_SPECS）。
AGENT_MODELS = {
    'deepseek-official': ('deepseek-flash', 'deepseek-v4-pro'),
    'minimax': ('MiniMax-M3.1-Flash', 'MiniMax-M3', 'MiniMax-M2.7', 'MiniMax-M2.7-highspeed'),
    'minimax-cn': ('MiniMax-M3.1-Flash', 'MiniMax-M3', 'MiniMax-M2.7', 'MiniMax-M2.7-highspeed'),
}
# 经 llm-pi-ai 激活的路由：Key 发往“账户地址的主机 + /anthropic”（和账户登记的主机一致，不另找地址）
PI_AI_ROUTES = ('minimax', 'minimax-cn')
AGENT_NOT_READY = '这个服务的办事（Harness）支持还没有验证'
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
    source: str = ''      # 这份设置在哪里改：“全局默认”或“角色单独设置”，出错提示里告诉用户
    base_url: str = ''    # llm-pi-ai 路由（MiniMax）的接口地址，写进运行时补丁；DeepSeek 用环境变量，这里留空


def anthropic_endpoint(base_url: str) -> str:
    """MiniMax 账户地址（OpenAI 兼容，如 https://api.minimaxi.com/v1）对应的 Anthropic 兼容地址：同一主机 + /anthropic。"""
    parts = urlsplit(str(base_url or ''))
    return f'{parts.scheme}://{parts.netloc}/anthropic' if parts.scheme and parts.netloc else ''


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
        raise ModelUnavailable(f'还没有配置{name}模型，请到设置的“模型与 Key”页选择。')

    def search_target(self, character_id: str) -> SearchTarget:
        config = self.store.load()
        for binding in (config.binding('search', character_id), config.binding('search', None)):
            if not binding: continue
            if binding.backend == 'deepseek' and binding.provider in config.providers:
                provider = config.providers[binding.provider]
                return SearchTarget('deepseek', provider.base_url, self.credentials.get(provider.credential, endpoint=provider.base_url))
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
            key = self.credentials.get(provider.credential, endpoint=provider.base_url)
            if not key: return None
            revision = self.credentials.revision(provider.credential)
            cache_id = '|'.join((provider.kind, provider.base_url, model.model_id, binding.voice, binding.language,
                                 f'{binding.speed:g}', f'{binding.volume:g}', binding.instructions, revision))
            return TtsTarget(provider.kind, provider.base_url.rstrip('/'), key, model.model_id, binding.voice, binding.language,
                             binding.speed, binding.volume, binding.instructions, cache_id,
                             f'{provider.label} · {model.model_id} · {binding.voice}')
        return None

    def agent_target(self, character_id: str | None = None) -> AgentTarget | None:
        """办事用的 Harness 路由、模型和只含选定凭据的环境变量。没配置、没有 Key 或模型不受支持时返回 None，
        原因用 agent_problem() 取。"""
        if self.offline: return None
        config = self.store.load()
        for source, binding in (('角色单独设置', config.binding('agent', character_id) if character_id else None),
                                ('全局默认', config.binding('agent', None))):
            if binding and binding.model in config.models and binding.route in AGENT_ROUTES:
                if config.models[binding.model].model_id not in AGENT_MODELS.get(binding.route, ()): return None
                model = config.models[binding.model]
                provider = config.providers.get(model.provider)
                if not provider: continue
                key = self.credentials.get(provider.credential, endpoint=provider.base_url)
                if not key: return None
                key_env, base_env = AGENT_ROUTES[binding.route]
                env = [(key_env, key)]
                if base_env and provider.base_url and provider.base_url.rstrip('/') != 'https://api.deepseek.com':
                    env.append((base_env, provider.base_url))
                base_url = anthropic_endpoint(provider.base_url) if binding.route in PI_AI_ROUTES else ''
                return AgentTarget(binding.route, model.model_id, tuple(env), f'{provider.label} · {model.model_id}', source, base_url)
        return None

    def agent_problem(self, character_id: str | None = None) -> str:
        """办事用不了时给用户看的原因：说清是哪一层设置（全局默认 / 角色单独设置）、该去哪里改。"""
        if self.offline: return '离线模式下不能办事。'
        config = self.store.load()
        for scope, binding in (('这个角色的单独设置', config.binding('agent', character_id) if character_id else None),
                               ('全局默认', config.binding('agent', None))):
            if not binding: continue
            model = config.models.get(binding.model)
            provider = config.providers.get(model.provider) if model else None
            where = f'设置 → 模型与 Key，把“设置范围”选成“{"全局默认" if scope == "全局默认" else "这个角色"}”，'
            if not model or not provider:
                return f'办事模型（{scope}）已被删除。请到{where}重新选择“办事”。'
            supported = AGENT_MODELS.get(binding.route, ())
            if not supported:
                return f'{AGENT_NOT_READY}。现在的办事模型 {model.model_id}（{scope}）用不了，请到{where}“办事”改选其他模型。'
            if model.model_id not in supported:
                return (f'办事模型 {model.model_id}（{scope}）Harness 不支持。请到{where}“办事”改选：'
                        + '、'.join(supported) + '。')
            if not self.credentials.get(provider.credential):
                return f'办事用的账户“{provider.label}”（{scope}）没有可用的 Key，请到设置 → 模型与 Key 修改。'
            return ''
        return '还没有配置办事模型：请到设置 → 模型与 Key，在“办事”里选择（全局默认对所有角色生效，也可以只给某个角色设置）。'

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
                # 只取和快照同一修订的 Key：快照之后账户换了地址和 Key，这次请求作废，不拼成“旧地址 + 新 Key”
                secret = self.credentials.get(resolved.credential, resolved.credential_revision, resolved.base_url)
                if not secret:
                    if self.credentials.get(resolved.credential):
                        raise ModelUnavailable(f'{resolved.label} 的 Key 刚刚更换，这次请求已取消，请再试一次。')
                    raise ModelUnavailable(f'{resolved.label} 的 Key 还没有设置或已失效，请到设置里填写。')
                client = (self._factory or openai_client)(resolved, secret)
                # 只保留每个“地址 + 模型”的最新凭据实例，旧凭据的实例随之丢弃
                for k in [k for k in self._clients if k[:3] == key[:3] and k[4] != key[4]]:
                    self._clients.pop(k, None)
                self._clients[key] = client
            return client

    def secret(self, resolved: Resolved) -> str:
        """构造请求时取 Key；不要保存或写进日志。只给和快照同一修订、登记在快照地址上的 Key，
        快照之后账户换了地址或 Key 时返回空串，调用方应放弃这次请求（Codex 审计 R1）。"""
        return self.credentials.get(resolved.credential, resolved.credential_revision, resolved.base_url)

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
                        limit, tuple(sorted((k, _freeze(v)) for k, v in extra.items())), context_window=model.context_window)


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
