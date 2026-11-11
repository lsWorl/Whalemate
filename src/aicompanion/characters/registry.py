"""角色注册表：把角色包同步到数据库，并为每个角色解析对应的大模型客户端。"""
from __future__ import annotations

import os

from aicompanion.characters import CharacterPack, discover
from aicompanion.domain.character import CharacterProfile

ACTIVE_KEY = 'active_character'
DEFAULT_KEY = 'v2_default_character'
DEFAULT_CHARACTER_ID = 'deepseek-whale'


class CharacterRegistry:
    def __init__(self, database, settings, packs: list[CharacterPack] | None = None, environ=None):
        self.database, self.settings = database, settings
        self.environ = os.environ if environ is None else environ
        if packs is None: packs, self.errors = discover()
        else: self.errors = []
        self.packs = {pack.id: pack for pack in packs}
        self._router = None

    def sync(self) -> None:
        """为每个就绪角色包补齐数据库行和初始语音设置；已有的用户修改不覆盖。"""
        existing = {character.id for character in self.database.characters()}
        for pack in self.packs.values():
            if not pack.ready: continue
            if pack.persona and pack.id not in existing:
                self.database.add_character(CharacterProfile(pack.name, pack.persona['description'], pack.persona['speaking_style'], pack.id))
            elif pack.persona and pack.id in existing: self._upgrade_persona(pack)
            if pack.id not in existing and pack.id not in {c.id for c in self.database.characters()}: continue
            language = pack.voice.get('language')
            if language and self.database.get_setting(f'speech_language:{pack.id}') is None:
                self.database.set_setting(f'speech_language:{pack.id}', language)
            voice_env = pack.voice.get('qwen_voice_env')
            voice = self.environ.get(voice_env, '').strip() if voice_env else ''
            if voice and self.database.get_setting(f'qwen_voice:{pack.id}') is None:
                self.database.set_setting(f'qwen_voice:{pack.id}', voice)
        # v2 首次启动默认切到鲸娘，只做一次；之后尊重用户的选择。
        if self.database.get_setting(DEFAULT_KEY) is None:
            if self.pack(DEFAULT_CHARACTER_ID) and any(c.id == DEFAULT_CHARACTER_ID for c in self.database.characters()):
                self.database.set_setting(ACTIVE_KEY, DEFAULT_CHARACTER_ID)
            self.database.set_setting(DEFAULT_KEY, True)

    def _upgrade_persona(self, pack) -> None:
        """角色包人设升级：只在数据库里还是上一版原文（用户没改过）时替换；替换前把旧的存一份。"""
        version = int(pack.persona.get('version') or 1)
        key = f'persona_version:{pack.id}'
        if int(self.database.get_setting(key, 1) or 1) >= version: return
        current = next((c for c in self.database.characters() if c.id == pack.id), None)
        if current is None: return
        # 上一版和更早的原文都认（用户可能跳过了某一版）；用户自己改过的人设不在其中，保持不动
        known = [(pack.persona.get('previous_description'), pack.persona.get('previous_speaking_style'))]
        known += [(o.get('description'), o.get('speaking_style')) for o in pack.persona.get('older') or [] if isinstance(o, dict)]
        if (current.description, current.speaking_style) in known:
            self.database.set_setting(f'persona_backup:{pack.id}', {'description': current.description, 'speaking_style': current.speaking_style})
            from dataclasses import replace
            self.database.update_character(replace(current, description=pack.persona['description'], speaking_style=pack.persona['speaking_style']))
        self.database.set_setting(key, version)

    def pack(self, character_id: str) -> CharacterPack | None:
        pack = self.packs.get(character_id)
        return pack if pack and pack.ready else None

    def characters(self) -> list[CharacterProfile]:
        """可选择的角色：数据库中的角色（含用户自建），按角色包顺序在前。"""
        order = {pack_id: pack.order for pack_id, pack in self.packs.items()}
        rows = self.database.characters()
        return sorted(rows, key=lambda c: (order.get(c.id, 1000), rows.index(c)))

    def active(self) -> CharacterProfile:
        characters = self.characters()
        if not characters: raise RuntimeError('数据库中没有任何角色。')
        wanted = self.database.get_setting(ACTIVE_KEY)
        return next((c for c in characters if c.id == wanted), characters[0])

    def set_active(self, character_id: str) -> None:
        self.database.set_setting(ACTIVE_KEY, character_id)

    @property
    def router(self):
        """模型解析器：按“角色单独指定 → 全局 → 角色包推荐 → 旧 .env”选模型（计划书 v4 功能一）。"""
        if self._router is None:
            from aicompanion.providers.router import ModelRouter
            self._router = ModelRouter(self.database, self.settings, packs=self.packs, environ=self.environ)
        return self._router

    def client_for(self, character_id: str, default_client):
        """返回 (客户端, 说明)。旧接口，内部交给 ModelRouter。

        结果落在旧 .env 配置时直接返回 default_client（同一份配置建出的同一个客户端），
        其余情况由解析器按地址、模型和凭据修订缓存客户端，换 Key 后下一次就用新实例。
        """
        if self.settings.llm_provider == 'mock':
            return default_client, '离线模拟模型'
        resolved = self.router.resolve('chat', character_id)
        label = resolved.label + (f'（{resolved.note}）' if resolved.note else '')
        if resolved.source == 'legacy':
            return default_client, label
        return self.router.client_for(resolved), label
