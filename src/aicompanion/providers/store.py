"""用户的模型配置：存在 app_settings 的一条 JSON 里，不新建表。

为什么不建表：配置量很小（几个账户、几个模型、十来条绑定），一条带版本号的 JSON 足够，
也不用动 storage/database.py 的表结构。每次保存递增 revision，请求快照会记下它。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

from aicompanion.providers.models import Binding, CAPABILITIES, ModelProfile, ProviderProfile

CONFIG_KEY = 'model_config'
CONFIG_VERSION = 1


@dataclass
class ModelConfig:
    providers: dict = field(default_factory=dict)   # id → ProviderProfile
    models: dict = field(default_factory=dict)      # id → ModelProfile
    bindings: list = field(default_factory=list)    # [Binding]
    revision: int = 0

    def binding(self, capability: str, character_id: str | None) -> Binding | None:
        return next((b for b in self.bindings if b.capability == capability and b.character_id == character_id), None)

    def users_of_provider(self, provider_id: str) -> list:
        models = {m.id for m in self.models.values() if m.provider == provider_id}
        return [b for b in self.bindings if b.model in models or b.provider == provider_id]


class ModelConfigStore:
    def __init__(self, database):
        self.db = database

    def load(self) -> ModelConfig:
        raw = self.db.get_setting(CONFIG_KEY) or {}
        if not isinstance(raw, dict) or int(raw.get('version', 0) or 0) > CONFIG_VERSION:
            # 比本程序新的配置：不解析也不覆盖，按“没有用户配置”运行，旧配置原样留着
            return ModelConfig(revision=int(raw.get('revision', 0) or 0) if isinstance(raw, dict) else 0)
        config = ModelConfig(revision=int(raw.get('revision', 0) or 0))
        for item in raw.get('providers') or []:
            try: profile = ProviderProfile.from_dict(item); config.providers[profile.id] = profile
            except (TypeError, KeyError): continue   # 坏的一条跳过，不影响其他
        for item in raw.get('models') or []:
            try: model = ModelProfile.from_dict(item); config.models[model.id] = model
            except (TypeError, KeyError): continue
        for item in raw.get('bindings') or []:
            try:
                binding = Binding.from_dict(item)
                if binding.capability in CAPABILITIES: config.bindings.append(binding)
            except (TypeError, KeyError): continue
        return config

    def save(self, config: ModelConfig) -> ModelConfig:
        config.revision += 1
        self.db.set_setting(CONFIG_KEY, {
            'version': CONFIG_VERSION, 'revision': config.revision,
            'providers': [asdict(p) for p in config.providers.values()],
            'models': [dict(asdict(m), capabilities=list(m.capabilities)) for m in config.models.values()],
            'bindings': [asdict(b) for b in config.bindings]})
        return config

    # ---- 常用修改：都是读出、改一处、整体写回 ------------------------------------------------
    def put_provider(self, profile: ProviderProfile) -> ModelConfig:
        config = self.load()
        old = config.providers.get(profile.id)
        config.providers[profile.id] = replace(profile, revision=(old.revision + 1) if old else profile.revision)
        return self.save(config)

    def put_model(self, model: ModelProfile) -> ModelConfig:
        config = self.load()
        if model.provider not in config.providers:
            raise ValueError('模型所属的账户不存在')
        old = config.models.get(model.id)
        config.models[model.id] = replace(model, revision=(old.revision + 1) if old else model.revision)
        return self.save(config)

    def bind(self, binding: Binding) -> ModelConfig:
        config = self.load()
        if binding.model and binding.model not in config.models:
            raise ValueError('绑定的模型不存在')
        config.bindings = [b for b in config.bindings
                           if not (b.capability == binding.capability and b.character_id == binding.character_id)]
        config.bindings.append(binding)
        return self.save(config)

    def unbind(self, capability: str, character_id: str | None) -> ModelConfig:
        """恢复继承：去掉这一条，回到上一层（全局 / 角色包推荐 / 旧配置）。"""
        config = self.load()
        config.bindings = [b for b in config.bindings if not (b.capability == capability and b.character_id == character_id)]
        return self.save(config)

    def remove_provider(self, provider_id: str) -> ModelConfig:
        config = self.load()
        if config.users_of_provider(provider_id):
            raise ValueError('还有能力在使用这个账户，请先改掉相关设置')
        config.providers.pop(provider_id, None)
        config.models = {k: m for k, m in config.models.items() if m.provider != provider_id}
        return self.save(config)
