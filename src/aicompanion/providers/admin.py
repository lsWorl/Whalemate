"""模型页背后的操作：添加账户与 Key、添加模型、按能力（可按角色）选择模型、测试连接。

和界面分开写：界面只负责展示和收集输入，这里的每个操作都能单独测试，诊断脚本也能复用。
"""
from __future__ import annotations

import re
from dataclasses import replace

from aicompanion.providers.models import (CAPABILITIES, MODEL_PRESETS, PROVIDER_PRESETS, Binding, ModelProfile,
                                          ProviderProfile)
from aicompanion.providers.router import AGENT_ROUTES, ModelRouter

CAPABILITY_LABELS = {'chat': '聊天', 'vision': '识图', 'search': '搜索', 'agent': '办事', 'tts': '语音合成'}
# 每种能力需要模型具备的标记；搜索不选模型，选的是账户
REQUIRES = {'chat': 'chat', 'vision': 'vision', 'agent': 'tools', 'tts': 'tts'}
TTS_LANGUAGES = (('', '自动判断'), ('zh', '中文'), ('en', '英文'), ('ja', '日语'))


def agent_route(provider: ProviderProfile) -> str | None:
    """Harness 内置路由（锁定的 DSH 0.2.1rc2）。其他服务的 Harness 支持未验证，不提供。"""
    if provider.kind == 'deepseek': return 'deepseek-official'
    if provider.kind == 'minimax': return 'minimax-cn' if provider.region == 'cn' else 'minimax'
    return None


class ModelAdmin:
    def __init__(self, router: ModelRouter):
        self.router = router
        self.store = router.store
        self.credentials = router.credentials

    # ---- 账户 --------------------------------------------------------------------------------
    def accounts(self) -> list[ProviderProfile]:
        return list(self.store.load().providers.values())

    def describe_key(self, provider: ProviderProfile) -> str:
        return self.credentials.describe(provider.credential)

    def add_account(self, preset: str = '', *, label: str = '', kind: str = 'openai_compatible', base_url: str = '',
                    region: str = '', billing: str = 'payg', key: str = '', key_env: str = '') -> ProviderProfile:
        """按预设（deepseek / minimax-global / minimax-cn / breezeblue）或自定义兼容服务添加账户。

        预设账户会一并加上它的预设模型。Key 二选一：key 加密保存，或 key_env 指向一个环境变量名。
        """
        base = PROVIDER_PRESETS.get(preset)
        profile = base or ProviderProfile('', label or '自定义服务', kind, base_url, region or 'custom', billing)
        if label: profile = replace(profile, label=label)
        if base_url: profile = replace(profile, base_url=base_url)
        if region: profile = replace(profile, region=region)
        if not profile.base_url.startswith('https://') and not profile.base_url.startswith('http://127.0.0.1'):
            raise ValueError('地址必须是 https://（本机调试可用 http://127.0.0.1）')
        if '@' in profile.base_url.split('//', 1)[-1].split('/', 1)[0]:
            raise ValueError('地址里不能带用户名或密码')
        profile = replace(profile, id=self._new_id(preset or profile.label))
        credential = self._credential(profile.id, key, key_env)
        if not credential:
            raise ValueError('请填写 Key，或填写保存 Key 的环境变量名')
        profile = replace(profile, credential=credential)
        self.store.put_provider(profile)
        if base:
            # 同一家服务的预设模型（国际站和国内站共用同一组模型）
            for model in MODEL_PRESETS.values():
                if PROVIDER_PRESETS[model.provider].kind == base.kind:
                    self.store.put_model(replace(model, id=self._model_id(profile.id, model.model_id), provider=profile.id))
        return profile

    def update_account(self, provider_id: str, *, label: str | None = None, base_url: str | None = None,
                       key: str = '', key_env: str = '') -> ProviderProfile:
        """改名、改地址、换 Key。Key 留空表示不修改（防止误点保存清掉 Key）。"""
        config = self.store.load()
        profile = config.providers[provider_id]
        if label: profile = replace(profile, label=label)
        if base_url:
            if not base_url.startswith('https://') and not base_url.startswith('http://127.0.0.1'):
                raise ValueError('地址必须是 https://')
            profile = replace(profile, base_url=base_url)
        if key or key_env:
            profile = replace(profile, credential=self._credential(provider_id, key, key_env))
        self.store.put_provider(profile)
        return profile

    def remove_account(self, provider_id: str) -> None:
        """删除账户和它的模型；还有能力在用时拒绝。保存的 Key 一并作废。"""
        profile = self.store.load().providers.get(provider_id)
        self.store.remove_provider(provider_id)
        if profile and profile.credential.startswith('stored:'):
            self.credentials.delete(profile.credential.split(':', 1)[1])

    # ---- 模型 --------------------------------------------------------------------------------
    def models(self, capability: str | None = None) -> list[ModelProfile]:
        config = self.store.load()
        need = REQUIRES.get(capability or '')
        rows = [m for m in config.models.values() if m.provider in config.providers]
        if need: rows = [m for m in rows if need in m.capabilities]
        if capability == 'agent':
            rows = [m for m in rows if agent_route(config.providers[m.provider])]
        return rows

    def add_model(self, provider_id: str, model_id: str, capabilities=('chat',), label: str = '') -> ModelProfile:
        """自定义模型：model_id 照填，能力由用户声明（服务返回的模型列表不等于能力证明）。"""
        model_id = model_id.strip()
        if not model_id: raise ValueError('模型名不能为空')
        unknown = set(capabilities) - {'chat', 'vision', 'tools', 'tts'}
        if unknown: raise ValueError('未知能力：' + '、'.join(sorted(unknown)))
        model = ModelProfile(self._model_id(provider_id, model_id), provider_id, model_id, label or model_id, tuple(capabilities))
        self.store.put_model(model)
        return model

    def remove_model(self, model_id: str) -> None:
        config = self.store.load()
        if any(b.model == model_id for b in config.bindings):
            raise ValueError('还有能力在用这个模型，请先改掉相关设置')
        config.models.pop(model_id, None)
        self.store.save(config)

    def model_label(self, model: ModelProfile) -> str:
        provider = self.store.load().providers.get(model.provider)
        return f'{provider.label if provider else "?"} · {model.label or model.model_id}'

    # ---- 能力选择 ----------------------------------------------------------------------------
    def binding(self, capability: str, character_id: str | None) -> Binding | None:
        return self.store.load().binding(capability, character_id)

    def choose(self, capability: str, character_id: str | None, model: str = '', **fields) -> None:
        """选择某个能力用的模型；model 为空表示清除（角色上清除 = 跟随全局）。

        search 用 backend / provider 字段；tts 可带 voice、language、speed、volume、instructions。
        """
        if capability not in CAPABILITIES: raise ValueError(f'未知能力：{capability}')
        if capability == 'search':
            backend = fields.get('backend', '')
            if not backend:
                self.store.unbind('search', character_id); return
            if backend == 'deepseek':
                provider = self.store.load().providers.get(fields.get('provider', ''))
                if not provider or provider.kind != 'deepseek':
                    raise ValueError('DeepSeek 搜索需要选择一个 DeepSeek 账户')
            self.store.bind(Binding('search', character_id=character_id, backend=backend, provider=fields.get('provider', '')))
            return
        if not model:
            self.store.unbind(capability, character_id); return
        config = self.store.load()
        profile = config.models.get(model)
        if not profile: raise ValueError('模型不存在')
        need = REQUIRES[capability]
        if need not in profile.capabilities:
            raise ValueError(f'{profile.model_id} 没有声明支持{CAPABILITY_LABELS[capability]}')
        extra = {}
        if capability == 'agent':
            route = agent_route(config.providers[profile.provider])
            if route not in AGENT_ROUTES: raise ValueError('这个服务的办事（Harness）支持还没有验证')
            extra['route'] = route
        if capability == 'tts':
            voice = str(fields.get('voice', '')).strip()
            if not voice: raise ValueError('请填写音色 ID')
            extra.update(voice=voice, language=fields.get('language', ''), speed=float(fields.get('speed', 1.0)),
                         volume=float(fields.get('volume', 1.0)), instructions=str(fields.get('instructions', '')).strip())
        self.store.bind(Binding(capability, model, character_id=character_id, **extra))

    # ---- 测试连接（会产生少量费用，由用户点按钮发起） -----------------------------------------
    def test_chat(self, character_id: str, cancellation=None) -> str:
        from aicompanion.domain.cancellation import CancellationToken
        from aicompanion.llm.base import ChatTurn
        if not self.router.configured('chat', character_id): raise ValueError('还没有选择聊天模型')
        client, resolved = self.router.client('chat', character_id, 'companion')
        reply = ''.join(client.generate_reply_stream([ChatTurn('user', '测试连接，请只回复“收到”。')], '你在做连接测试。',
                                                     cancellation or CancellationToken()))
        return f'{resolved.label}：{reply.strip()[:40]}'

    def test_tts(self, character_id: str, cancellation=None) -> bytes:
        from aicompanion.domain.cancellation import CancellationToken
        from aicompanion.speech.breeze_client import BreezeSpeechClient
        target = self.router.tts_target(character_id)
        if target is None: raise ValueError('还没有配置语音合成（模型、音色和 Key）')
        return BreezeSpeechClient().synthesize(target, '你好，这是声音测试。', cancellation or CancellationToken())

    # ---- 内部 --------------------------------------------------------------------------------
    def _credential(self, provider_id: str, key: str, key_env: str) -> str:
        if key.strip():
            return self.credentials.save(provider_id, key)
        if key_env.strip():
            name = key_env.strip()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name): raise ValueError('环境变量名不合法')
            return 'env:' + name
        return ''

    def _new_id(self, seed: str) -> str:
        base = re.sub(r'[^a-z0-9-]+', '-', seed.lower()).strip('-') or 'account'
        existing = set(self.store.load().providers)
        if base not in existing: return base
        n = 2
        while f'{base}-{n}' in existing: n += 1
        return f'{base}-{n}'

    @staticmethod
    def _model_id(provider_id: str, model_id: str) -> str:
        return f'{provider_id}/{model_id}'
