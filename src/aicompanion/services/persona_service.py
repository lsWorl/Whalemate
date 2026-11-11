"""人格包的存取、切换、版本和角色包升级（计划书 v4 功能二）。

和角色的关系：角色表里的 description / speaking_style 始终等于“当前人格”的同名字段——桌面、QQ、语音、办事原本就读这两项，
切换人格只要改这两项，下一句话就是新性格，不用每个调用点各自查人格。背景、示例、世界书由 ContextService 另外带上。
记忆、关系、表情、办事记录都按角色 ID 存，与人格无关，切换不丢（长期架构约束 2）。
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from uuid import uuid4

from aicompanion.domain.persona import Persona, PersonaError, normalize

ACTIVE_KEY = 'active_persona:{}'
KEEP_VERSIONS = 30


def builtin_id(character_id: str) -> str:
    return f'builtin:{character_id}'


class PersonaService:
    def __init__(self, database, clock=time.time):
        self.db, self.clock = database, clock

    # ---- 读取 ------------------------------------------------------------------------------
    def personas(self, character_id: str) -> list[Persona]:
        with self.db.transaction() as c:
            rows = c.execute('SELECT * FROM personas WHERE character_id=? ORDER BY builtin DESC, created_at, id', (character_id,)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, persona_id: str) -> Persona | None:
        with self.db.transaction() as c:
            row = c.execute('SELECT * FROM personas WHERE id=?', (persona_id,)).fetchone()
        return self._row(row) if row else None

    def active(self, character_id: str) -> Persona | None:
        wanted = self.db.get_setting(ACTIVE_KEY.format(character_id))
        rows = self.personas(character_id)
        return next((p for p in rows if p.id == wanted), rows[0] if rows else None)

    def versions(self, persona_id: str) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute('SELECT id, note, saved_at, data FROM persona_versions WHERE persona_id=? ORDER BY id DESC', (persona_id,)).fetchall()
        return [{'id': r['id'], 'note': r['note'], 'saved_at': r['saved_at'], 'content': json.loads(r['data'])} for r in rows]

    # ---- 首次启用与角色包升级 ----------------------------------------------------------------------
    def ensure(self, character, pack_persona: dict | None = None) -> Persona:
        """保证角色至少有一个人格，并按角色包同步内置人格。返回当前人格。

        第一次启用：有角色包时建内置人格（包里的原文）。角色表里的设定如果和包里的一样，内置人格就是当前人格；
        不一样（用户以前改过），另建“我改过的版本”作为当前人格，内置原文保留，随时可以切回。没有角色包的自建角色建一个“默认”。
        角色包升级：内置人格没被用户改过时更新为新原文（留一个版本）；改过的不覆盖。"""
        current = {'title': '默认', 'description': character.description, 'speaking_style': character.speaking_style}
        existing = self.personas(character.id)
        if pack_persona:
            pack = {'title': '默认（内置）', 'description': pack_persona.get('description', ''),
                    'speaking_style': pack_persona.get('speaking_style', ''), **{k: pack_persona[k] for k in
                    ('background', 'examples', 'interests', 'temper', 'lore') if k in pack_persona}}
            version = int(pack_persona.get('version') or 1)
            built = next((p for p in existing if p.builtin), None)
            if built is None:
                built = self._insert(Persona(builtin_id(character.id), character.id, '', builtin=True, pack_version=version)
                                     .with_content(pack), '角色包内置')
                if not existing:
                    same = (character.description, character.speaking_style) == (built.description, built.speaking_style)
                    if not same and (character.description or character.speaking_style):
                        mine = self.create(character.id, dict(current, title='我改过的版本'), note='启用人格包前的设定')
                        self._set_active(character.id, mine.id)
                    else:
                        self._set_active(character.id, built.id)
            elif version > built.pack_version:
                if built.user_modified:
                    self.db.set_setting(f'persona_pack_update:{character.id}', version)   # 不覆盖；界面提示“内置版本有更新”
                else:
                    self.save(replace(built, pack_version=version).with_content(pack), note=f'角色包升级到第 {version} 版', by_user=False)
        elif not existing:
            mine = self.create(character.id, current)
            self._set_active(character.id, mine.id)
        return self.active(character.id)

    def pack_update_pending(self, character_id: str) -> bool:
        return bool(self.db.get_setting(f'persona_pack_update:{character_id}'))

    def restore_builtin(self, character_id: str, pack_persona: dict) -> Persona:
        """内置人格恢复成角色包当前原文（用户主动点的；当前内容先留一个版本）。"""
        built = self.get(builtin_id(character_id))
        if built is None: raise PersonaError('这个角色没有内置人格')
        data = {'description': pack_persona.get('description', ''), 'speaking_style': pack_persona.get('speaking_style', ''),
                'background': '', 'examples': [], 'lore': [], 'interests': [], 'title': '默认（内置）'}
        restored = self.save(replace(built, user_modified=False, pack_version=int(pack_persona.get('version') or 1)).with_content(data),
                             note='恢复内置原文', by_user=False)
        self.db.set_setting(f'persona_pack_update:{character_id}', 0)
        return restored

    # ---- 修改 ------------------------------------------------------------------------------
    def create(self, character_id: str, content: dict, note: str = '新建') -> Persona:
        now = self.clock()
        persona = Persona(uuid4().hex, character_id, '').with_content(dict({'title': '新人格'}, **content))
        return self._insert(persona, note, now)

    def duplicate(self, persona_id: str) -> Persona:
        source = self.get(persona_id)
        if source is None: raise PersonaError('人格不存在')
        return self.create(source.character_id, dict(source.content(), title=(source.title+' 副本')[:40]), note=f'复制自“{source.title}”')

    def save(self, persona: Persona, note: str = '保存', by_user: bool = True) -> Persona:
        """保存修改并留一个版本。内容没变就不留版本。当前人格同步到角色表。"""
        old = self.get(persona.id)
        if old is None: raise PersonaError('人格不存在')
        if by_user and persona.builtin and old.content() != persona.content(): persona = replace(persona, user_modified=True)
        if old.content() == persona.content() and (old.user_modified, old.pack_version) == (persona.user_modified, persona.pack_version):
            return old
        self._update(persona, note)
        if self.active(persona.character_id).id == persona.id: self._mirror(persona)
        return self.get(persona.id)

    def switch(self, character_id: str, persona_id: str) -> Persona:
        persona = self.get(persona_id)
        if persona is None or persona.character_id != character_id: raise PersonaError('人格不存在')
        self._set_active(character_id, persona_id); self._mirror(persona)
        return persona

    def delete(self, persona_id: str) -> None:
        persona = self.get(persona_id)
        if persona is None: return
        if persona.builtin: raise PersonaError('内置人格不能删除（可以改，也可以恢复原文）')
        if self.active(persona.character_id).id == persona_id: raise PersonaError('正在用的人格不能删除，请先切换到别的人格')
        with self.db.transaction() as c:
            c.execute('DELETE FROM personas WHERE id=?', (persona_id,)); c.execute('DELETE FROM persona_versions WHERE persona_id=?', (persona_id,))

    def revert(self, persona_id: str, version_id: int) -> Persona:
        persona = self.get(persona_id)
        version = next((v for v in self.versions(persona_id) if v['id'] == version_id), None)
        if persona is None or version is None: raise PersonaError('版本不存在')
        return self.save(persona.with_content(version['content']), note=f'回退到版本 {version_id}')

    def import_content(self, character_id: str, content: dict) -> Persona:
        """导入的内容新建为一个人格（不会覆盖已有人格，也不会自动切换）。"""
        return self.create(character_id, content, note='导入')

    # ---- 内部 ------------------------------------------------------------------------------
    def _mirror(self, persona: Persona) -> None:
        """当前人格写回角色表的设定和说话风格（名字、形象、声音不动）。"""
        character = next((c for c in self.db.characters() if c.id == persona.character_id), None)
        if character is None: return
        if (character.description, character.speaking_style) != (persona.description, persona.speaking_style):
            self.db.update_character(replace(character, description=persona.description, speaking_style=persona.speaking_style))

    def _set_active(self, character_id: str, persona_id: str) -> None:
        self.db.set_setting(ACTIVE_KEY.format(character_id), persona_id)

    def _insert(self, persona: Persona, note: str, now: float | None = None) -> Persona:
        now = self.clock() if now is None else now
        with self.db.transaction() as c:
            c.execute('INSERT INTO personas(id,character_id,title,data,builtin,pack_version,user_modified,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                      (persona.id, persona.character_id, persona.title, json.dumps(persona.content(), ensure_ascii=False),
                       int(persona.builtin), persona.pack_version, int(persona.user_modified), now, now))
            self._version(c, persona, note, now)
        return self.get(persona.id)

    def _update(self, persona: Persona, note: str) -> None:
        now = self.clock()
        with self.db.transaction() as c:
            c.execute('UPDATE personas SET title=?, data=?, pack_version=?, user_modified=?, updated_at=? WHERE id=?',
                      (persona.title, json.dumps(persona.content(), ensure_ascii=False), persona.pack_version,
                       int(persona.user_modified), now, persona.id))
            if note: self._version(c, persona, note, now)

    @staticmethod
    def _version(c, persona: Persona, note: str, now: float) -> None:
        c.execute('INSERT INTO persona_versions(persona_id,data,note,saved_at) VALUES (?,?,?,?)',
                  (persona.id, json.dumps(persona.content(), ensure_ascii=False), note, now))
        c.execute('DELETE FROM persona_versions WHERE persona_id=? AND id NOT IN '
                  '(SELECT id FROM persona_versions WHERE persona_id=? ORDER BY id DESC LIMIT ?)', (persona.id, persona.id, KEEP_VERSIONS))

    @staticmethod
    def _row(row) -> Persona:
        persona = Persona(row['id'], row['character_id'], row['title'], builtin=bool(row['builtin']),
                          pack_version=int(row['pack_version']), user_modified=bool(row['user_modified']))
        try: return persona.with_content(json.loads(row['data']))
        except (ValueError, PersonaError): return persona
