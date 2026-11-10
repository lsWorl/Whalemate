"""按“角色 + 能力 + 用途”解析出这一次请求用哪个服务、哪个模型、哪把 Key。

查找顺序（计划书 v4 功能一）：
1. 用户给这个角色单独指定的；
2. 用户的全局设置；
3. 角色包推荐（例如鲸娘包推荐 DeepSeek 专属 Key，读环境变量）；
4. 旧 .env 里的 LLM_* 配置。

用户什么都没设置时，结果和升级前完全一样，所以升级不需要搬数据。
解析结果是冻结的快照（Resolved），后台线程拿着它把这一轮说完；换 Key、换模型只影响下一次请求。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.models import (DEFAULT_MAX_OUTPUT_WHEN_THINKING, DEFAULT_THINKING, PURPOSE_KIND,
                                          ModelProfile, ProviderProfile, Resolved, kind_for_url)
from aicompanion.providers.store import ModelConfigStore

LEGACY_PROVIDER = 'legacy-llm'
# Harness 路由 → 它读取的环境变量（锁定的 DSH 0.2.1rc2 内置；新路由验证后再加）
AGENT_ROUTES = {
    'deepseek-official': ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL'),
    'minimax': ('MINIMAX_API_KEY', None),
    'minimax-cn': ('MINIMAX_CN_API_KEY', None),
}


class ModelUnavailable(RuntimeError):
    """配置了但现在用不了（例如 Key 没填）。不偷偷换到别的收费服务，交给界面提示。"""


@dataclass(frozen=True)
class SearchTarget:
    backend: str          # deepseek / wikipedia
    base_url: str = ''
    api_key: str = ''


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


class ModelRouter:
    def __init__(self, database, settings, packs=None, environ=None, credentials=None, mock_client=None,
                 client_factory=None):
        self.db, self.settings = database, settings
        self.packs = packs if packs is not None else {}
        if credentials is None:
            import os
            from collections import ChainMap
            # 旧配置的 Key 在 Settings 里；环境变量优先，没有时用 Settings 的值（与升级前一致）
            legacy = {'LLM_API_KEY': str(getattr(settings, 'llm_api_key', '') or '')}
            credentials = CredentialStore(database, ChainMap(os.environ if environ is None else environ, legacy))
        self.credentials = credentials
        self.store = ModelConfigStore(database)
        self.mock_client = mock_client
        self._factory = client_factory
        self._clients = {}
        self._lock = threading.Lock()

    @property
    def offline(self) -> bool:
        return getattr(self.settings, 'llm_provider', '') == 'mock'

    # ---- 解析 --------------------------------------------------------------------------------
    def resolve(self, capability: str, character_id: str, purpose: str = 'companion') -> Resolved:
        if capability == 'vision':
            return self._resolve_vision(character_id, purpose)
        if capability != 'chat':
            raise ValueError(f'resolve 不处理 {capability}，请用 search_target / agent_target')
        config = self.store.load()
        for source, binding in (('character', config.binding('chat', character_id)), ('global', config.binding('chat', None))):
            if binding and binding.model in config.models:
                model = config.models[binding.model]
                provider = config.providers.get(model.provider)
                if provider:
                    return self._make('chat', character_id, purpose, source, provider, model, config.revision)
        return self._fallback_chat(character_id, purpose, config.revision)

    def _fallback_chat(self, character_id, purpose, revision) -> Resolved:
        """第 3、4 层：角色包推荐和旧 .env，行为与升级前的 CharacterRegistry.client_for 一致。"""
        legacy = self._legacy_provider()
        pack = self.packs.get(character_id)
        spec = getattr(pack, 'provider', None) or {}   # 与旧逻辑一致：形象未就绪的角色包也读它的模型推荐
        if spec.get('host') and not self.offline:
            env = self.credentials.environ
            key_env = spec.get('key_env') or ''
            base = (env.get(spec['base_url_env'], '') if spec.get('base_url_env') else '') or spec.get('base_url', '')
            model_id = (env.get(spec['model_env'], '') if spec.get('model_env') else '') or spec.get('default_model', '')
            label = spec.get('label', '默认模型')
            if key_env and self.credentials.available('env:' + key_env) and base.strip() and model_id.strip():
                provider = ProviderProfile('pack:' + character_id, label, kind_for_url(base), base.strip(),
                                           credential='env:' + key_env)
                return self._make('chat', character_id, purpose, 'pack', provider, self._implicit_model(provider, model_id.strip()),
                                  revision)
            if urlsplit(legacy.base_url).hostname == spec['host']:
                return self._make('chat', character_id, purpose, 'legacy', legacy, self._legacy_model(), revision, label=label)
            return self._make('chat', character_id, purpose, 'legacy', legacy, self._legacy_model(), revision,
                              label='默认模型', note=f'未配置 {label} 专属密钥')
        label = spec.get('label', '默认模型') if spec else '默认模型'
        return self._make('chat', character_id, purpose, 'legacy', legacy, self._legacy_model(), revision, label=label)

    def _resolve_vision(self, character_id, purpose) -> Resolved:
        config = self.store.load()
        for source, binding in (('character', config.binding('vision', character_id)), ('global', config.binding('vision', None))):
            if binding and binding.model in config.models:
                model = config.models[binding.model]
                provider = config.providers.get(model.provider)
                if provider:
                    return self._make('vision', character_id, purpose, source, provider, model, config.revision)
        # 旧规则（bridge/media.py 的 vision_model）：跟随聊天的服务和 Key；DeepSeek 官方用设置里的识图模型，
        # 其他服务在设置还停在 deepseek 系列时改用聊天模型本身。
        chat = self.resolve('chat', character_id, purpose)
        chosen = str(self.db.get_setting('qq_vision_model', 'deepseek-flash') or '').strip()
        if urlsplit(chat.base_url).hostname == 'api.deepseek.com': model_id = chosen or 'deepseek-flash'
        else: model_id = chosen if chosen and not chosen.startswith('deepseek') else chat.model_id
        provider = ProviderProfile(chat.provider_id, chat.label.split(' · ')[0], chat.provider_kind, chat.base_url,
                                   credential=chat.credential)
        return self._make('vision', character_id, purpose, chat.source, provider,
                          self._implicit_model(provider, model_id, temperature=0.0), chat.config_revision)

    def search_target(self, character_id: str) -> SearchTarget:
        config = self.store.load()
        for binding in (config.binding('search', character_id), config.binding('search', None)):
            if not binding: continue
            if binding.backend == 'deepseek' and binding.provider in config.providers:
                provider = config.providers[binding.provider]
                return SearchTarget('deepseek', provider.base_url, self.credentials.get(provider.credential))
            if binding.backend == 'wikipedia':
                return SearchTarget('wikipedia')
        # 旧规则（community/search.py 的 backend）：聊天走 DeepSeek 官方 https 且有 Key 才用 DeepSeek 搜索
        if self.offline: return SearchTarget('wikipedia')
        chat = self.resolve('chat', character_id, 'community')
        parts = urlsplit(chat.base_url)
        key = self.credentials.get(chat.credential)
        if parts.scheme == 'https' and parts.hostname == 'api.deepseek.com' and key:
            return SearchTarget('deepseek', chat.base_url, key)
        return SearchTarget('wikipedia')

    def agent_target(self, character_id: str | None = None) -> AgentTarget | None:
        """办事用的 Harness 路由、模型和只含选定凭据的环境变量。没有可用 Key 返回 None。"""
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
                if base_env and provider.base_url and urlsplit(provider.base_url).hostname != 'api.deepseek.com':
                    env.append((base_env, provider.base_url))
                return AgentTarget(binding.route, model.model_id, tuple(env), f'{provider.label} · {model.model_id}')
        # 旧规则（ui/agent_session.py）：全局 LLM Key + 设置里的 dsh_model，走 deepseek-official
        if self.offline: return None
        key = self.credentials.get('env:LLM_API_KEY') or str(getattr(self.settings, 'llm_api_key', '') or '')
        if not key: return None
        from aicompanion.agent.dsh_backend import DEFAULT_MODEL, dsh_base_url
        model_id = self.db.get_setting('dsh_model', DEFAULT_MODEL) or DEFAULT_MODEL
        env = [('DEEPSEEK_API_KEY', key)]
        base = dsh_base_url(getattr(self.settings, 'llm_base_url', ''))
        if base: env.append(('DEEPSEEK_BASE_URL', base))
        return AgentTarget('deepseek-official', model_id, tuple(env), f'DeepSeek · {model_id}')

    # ---- 客户端 ------------------------------------------------------------------------------
    def client(self, capability: str, character_id: str, purpose: str = 'companion'):
        """返回 (客户端, 快照)。同样的地址、模型、凭据修订和参数复用同一个实例；任何一项变了就换新的。"""
        if self.offline:
            if self.mock_client is None:
                from aicompanion.llm.mock_client import MockLLMClient
                self.mock_client = MockLLMClient()
            return self.mock_client, self.resolve(capability, character_id, purpose)
        resolved = self.resolve(capability, character_id, purpose)
        return self.client_for(resolved), resolved

    def client_for(self, resolved: Resolved):
        key = resolved.cache_key
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                secret = self.credentials.get(resolved.credential)
                if not secret:
                    raise ModelUnavailable(f'{resolved.label} 的 Key 还没有设置或已失效，请到设置里填写。')
                factory = self._factory or _openai_client
                client = factory(resolved, secret)
                # 只保留每个“地址 + 模型 + 参数”的最新实例，旧凭据的实例随之丢弃
                stale = [k for k in self._clients if k[:3] == key[:3] and k[4] != key[4]]
                for k in stale: self._clients.pop(k, None)
                self._clients[key] = client
            return client

    def label(self, character_id: str) -> str:
        if self.offline: return '离线模拟模型'
        resolved = self.resolve('chat', character_id)
        return resolved.label + (f'（{resolved.note}）' if resolved.note else '')

    # ---- 内部 ------------------------------------------------------------------------------
    def _legacy_provider(self) -> ProviderProfile:
        base = str(getattr(self.settings, 'llm_base_url', '') or '')
        return ProviderProfile(LEGACY_PROVIDER, '默认模型', kind_for_url(base), base, credential='env:LLM_API_KEY')

    def _legacy_model(self) -> ModelProfile:
        return self._implicit_model(self._legacy_provider(), str(getattr(self.settings, 'llm_model', '') or ''))

    @staticmethod
    def _implicit_model(provider: ProviderProfile, model_id: str, temperature=None) -> ModelProfile:
        return ModelProfile('implicit:' + model_id, provider.id, model_id, model_id, temperature=temperature)

    def _make(self, capability, character_id, purpose, source, provider, model, revision, label=None, note='') -> Resolved:
        extra, limit = thinking_params(provider.kind, model, purpose)
        temperature = model.temperature if model.temperature is not None else float(getattr(self.settings, 'llm_temperature', 0.8))
        name = label or provider.label
        return Resolved(capability, character_id, purpose, source, provider.id, provider.kind, provider.base_url.rstrip('/'),
                        model.model_id, provider.credential, self.credentials.revision(provider.credential), revision,
                        f'{name} · {model.model_id}', temperature, int(getattr(self.settings, 'llm_timeout_seconds', 60)),
                        limit, tuple(sorted((k, _freeze(v)) for k, v in extra.items())), note)


def _freeze(value):
    return tuple(sorted((k, _freeze(v)) for k, v in value.items())) if isinstance(value, dict) else value


def _thaw(value):
    if isinstance(value, tuple) and all(isinstance(i, tuple) and len(i) == 2 for i in value):
        return {k: _thaw(v) for k, v in value}
    return value


def _openai_client(resolved: Resolved, secret: str):
    from aicompanion.llm.openai_compatible_client import OpenAICompatibleClient
    return OpenAICompatibleClient(base_url=resolved.base_url, api_key=secret, model=resolved.model_id,
                                  temperature=resolved.temperature, timeout_seconds=resolved.timeout_seconds,
                                  extra=_thaw(resolved.extra), max_tokens=resolved.max_output)
