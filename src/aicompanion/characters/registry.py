"""角色注册表：把角色包同步到数据库，并为每个角色解析对应的大模型客户端。"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

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
        self._clients = {}

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
        old = (pack.persona.get('previous_description'), pack.persona.get('previous_speaking_style'))
        if (current.description, current.speaking_style) == old:
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

    def client_for(self, character_id: str, default_client):
        """返回 (客户端, 说明)。

        优先使用角色包声明的专属密钥；没有专属密钥但默认模型就是同一家时直接复用；
        否则退回默认模型，并在说明中如实标出。
        """
        pack = self.packs.get(character_id)
        provider = pack.provider if pack else {}
        label = provider.get('label', '默认模型')
        if self.settings.llm_provider == 'mock' or not provider.get('host'):
            return default_client, f'{label} · {self.settings.llm_model}' if self.settings.llm_provider != 'mock' else '离线模拟模型'
        key = self.environ.get(provider.get('key_env', ''), '').strip() if provider.get('key_env') else ''
        base = (self.environ.get(provider.get('base_url_env', ''), '') if provider.get('base_url_env') else '') or provider.get('base_url', '')
        model = (self.environ.get(provider.get('model_env', ''), '') if provider.get('model_env') else '') or provider.get('default_model', '')
        if key and base and model:
            cache_key = (character_id, base, model)
            if cache_key not in self._clients:
                from aicompanion.llm.openai_compatible_client import OpenAICompatibleClient
                self._clients[cache_key] = OpenAICompatibleClient(base_url=base.strip(), api_key=key, model=model.strip(),
                    temperature=self.settings.llm_temperature, timeout_seconds=self.settings.llm_timeout_seconds)
            return self._clients[cache_key], f'{label} · {model.strip()}'
        if urlsplit(self.settings.llm_base_url).hostname == provider['host']:
            return default_client, f'{label} · {self.settings.llm_model}'
        return default_client, f'默认模型 · {self.settings.llm_model}（未配置 {label} 专属密钥）'
