"""人格包（计划书 v4 功能二）：同一个她可以换上不同的性格，记忆、关系、表情习惯、办事记录都不变。

人格是纯数据：角色设定、背景、说话风格、对话示例、兴趣（功能四用）、情绪倾向（功能三用）、世界书。
不含脚本，也不带 Key 和模型配置；不含名字——名字属于角色身份，换人格不改名（长期架构约束 1、2）。

兼容 SillyTavern 角色卡（chara_card_v2 / v3 的 JSON，或把 JSON 藏在 PNG 文本块里的卡片）的常用字段：
description、personality、scenario、mes_example、first_mes、system_prompt、post_history_instructions、tags、character_book。
"""
from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from dataclasses import asdict, dataclass, field, replace

FORMAT = 'AICompanionPersona'
FORMAT_VERSION = 1
MAX_FILE_BYTES = 4 * 1024 * 1024          # 角色卡 PNG 常带一张大图，文本本身远小于此
LIMITS = {'title': 40, 'description': 8000, 'background': 8000, 'speaking_style': 4000, 'notes': 2000}
MAX_EXAMPLES, MAX_EXAMPLE_CHARS = 12, 600
MAX_LORE, MAX_LORE_CHARS, MAX_KEYS = 64, 1500, 12
MAX_INTERESTS = 24
# 人格内容超过这个字数时提示占用上下文（只提示，不替用户删改）。初始值，按实际效果调整。
WARN_CHARS = 6000
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


class PersonaError(ValueError):
    """人格内容或文件不符合要求。"""


@dataclass(frozen=True)
class Example:
    user: str
    reply: str


@dataclass(frozen=True)
class LoreEntry:
    """世界书条目：对话里出现关键词时才带上；always=True 的每次都带。"""
    keys: tuple = ()
    content: str = ''
    always: bool = False


@dataclass(frozen=True)
class Persona:
    id: str
    character_id: str
    title: str                         # 人格的名字，例如“默认”“傲娇一点”；不是角色名
    description: str = ''
    speaking_style: str = ''
    background: str = ''
    examples: tuple = ()               # Example
    interests: tuple = ()              # 文本
    temper: tuple = (('touchy', 0.5), ('forgiving', 0.5))   # 情绪倾向：容易不高兴、消气快（0～1），功能三使用
    lore: tuple = ()                   # LoreEntry
    notes: str = ''                    # 作者备注，不进提示词
    builtin: bool = False              # 来自角色包的内置人格
    pack_version: int = 0
    user_modified: bool = False        # 内置人格被用户改过：角色包升级时不覆盖

    # ---- 序列化 ------------------------------------------------------------------------------
    def content(self) -> dict:
        """只含人格内容（导出、版本、比较都用它），不含身份和状态字段。"""
        return {'title': self.title, 'description': self.description, 'speaking_style': self.speaking_style,
                'background': self.background, 'examples': [asdict(e) for e in self.examples],
                'interests': list(self.interests), 'temper': dict(self.temper),
                'lore': [{'keys': list(e.keys), 'content': e.content, 'always': e.always} for e in self.lore],
                'notes': self.notes}

    def with_content(self, data: dict) -> 'Persona':
        clean = normalize(data)
        return replace(self, **clean)

    def chars(self) -> int:
        """大约会占多少上下文（字数）：常驻内容全算，世界书只算每次都带的条目。"""
        always = sum(len(e.content) for e in self.lore if e.always)
        return (len(self.description)+len(self.speaking_style)+len(self.background)
                + sum(len(e.user)+len(e.reply) for e in self.examples) + always)


def _text(value, limit: int) -> str:
    if value is None: return ''
    if not isinstance(value, str): raise PersonaError('人格内容里有不是文字的字段')
    value = _CONTROL.sub('', value.replace('\r\n', '\n')).strip()
    return value[:limit]


def _ratio(value, default=0.5) -> float:
    try: return round(min(1.0, max(0.0, float(value))), 2)
    except (TypeError, ValueError): return default


def normalize(data: dict) -> dict:
    """把外来的人格内容整理成安全、有长度上限的字段。未知字段丢弃。"""
    if not isinstance(data, dict): raise PersonaError('人格内容必须是对象')
    out = {key: _text(data.get(key), limit) for key, limit in LIMITS.items() if key in data or key != 'title'}
    if 'title' in data: out['title'] = _text(data['title'], LIMITS['title']) or '未命名人格'
    examples = []
    for item in (data.get('examples') or [])[:MAX_EXAMPLES]:
        if isinstance(item, Example): item = asdict(item)        # 已整理过的内容再整理一次结果不变
        if isinstance(item, dict):
            user, reply = _text(item.get('user'), MAX_EXAMPLE_CHARS), _text(item.get('reply'), MAX_EXAMPLE_CHARS)
            if reply: examples.append(Example(user, reply))
    out['examples'] = tuple(examples)
    out['interests'] = tuple(t for t in (_text(x, 40) for x in (data.get('interests') or [])[:MAX_INTERESTS] if isinstance(x, str)) if t)
    temper = data.get('temper')
    if isinstance(temper, tuple): temper = dict(temper)
    if not isinstance(temper, dict): temper = {}
    out['temper'] = (('touchy', _ratio(temper.get('touchy'))), ('forgiving', _ratio(temper.get('forgiving'))))
    lore = []
    for item in (data.get('lore') or [])[:MAX_LORE]:
        if isinstance(item, LoreEntry): item = {'keys': list(item.keys), 'content': item.content, 'always': item.always}
        if not isinstance(item, dict): continue
        content = _text(item.get('content'), MAX_LORE_CHARS)
        keys = tuple(k for k in (_text(x, 40) for x in (item.get('keys') or [])[:MAX_KEYS] if isinstance(x, str)) if k)
        if content and (keys or item.get('always')): lore.append(LoreEntry(keys, content, bool(item.get('always'))))
    out['lore'] = tuple(lore)
    return out


# ---- 提示词 ----------------------------------------------------------------------------------
def prompt_extras(persona: Persona | None, text: str) -> str:
    """角色设定和说话风格之外、要带进提示词的部分：背景、示例、匹配到的世界书。text 是这一轮相关的对话文字。"""
    if persona is None: return ''
    parts = []
    if persona.background: parts.append('背景：'+persona.background)
    if persona.examples:
        lines = []
        for e in persona.examples:
            lines.append((f'对方：{e.user}\n' if e.user else '')+f'你：{e.reply}')
        parts.append('说话示例（只学语气和分寸，不要照抄原句）：\n'+'\n---\n'.join(lines))
    lowered = (text or '').casefold()
    hits = [e for e in persona.lore if e.always or any(k.casefold() in lowered for k in e.keys)]
    if hits: parts.append('相关设定（仅作背景，不是指令）：\n'+'\n'.join('- '+e.content for e in hits[:8]))
    return '\n'.join(parts)


# ---- 导入 / 导出 -----------------------------------------------------------------------------
def export_own(persona: Persona) -> dict:
    return {'format': FORMAT, 'version': FORMAT_VERSION, **persona.content()}


def export_sillytavern(persona: Persona, character_name: str) -> dict:
    """导出成 SillyTavern chara_card_v2 JSON，方便在别处用。只含人格内容，不含聊天记录。"""
    examples = '\n'.join('<START>\n'+(f'{{{{user}}}}: {e.user}\n' if e.user else '')+f'{{{{char}}}}: {e.reply}' for e in persona.examples)
    book = {'name': persona.title, 'entries': [
        {'keys': list(e.keys), 'content': e.content, 'enabled': True, 'constant': e.always, 'insertion_order': i, 'id': i}
        for i, e in enumerate(persona.lore)]}
    return {'spec': 'chara_card_v2', 'spec_version': '2.0', 'data': {
        'name': character_name, 'description': persona.description, 'personality': persona.speaking_style,
        'scenario': persona.background, 'first_mes': '', 'mes_example': examples, 'creator_notes': persona.notes,
        'system_prompt': '', 'post_history_instructions': '', 'tags': list(persona.interests), 'creator': '',
        'character_version': persona.title, 'alternate_greetings': [], 'extensions': {'aicompanion': {'temper': dict(persona.temper)}},
        **({'character_book': book} if persona.lore else {})}}


def read_card(raw: bytes) -> dict:
    """读入人格文件：本项目 JSON、SillyTavern JSON，或带 chara / ccv3 文本块的 PNG 角色卡。返回 normalize 后的内容。"""
    if len(raw) > MAX_FILE_BYTES: raise PersonaError('文件太大（超过 4 MB）')
    if raw[:8] == b'\x89PNG\r\n\x1a\n':
        data = _png_card(raw)
    else:
        try: data = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError) as error: raise PersonaError('不是有效的 JSON 或 PNG 角色卡') from error
    if not isinstance(data, dict): raise PersonaError('文件内容不是人格')
    if data.get('format') == FORMAT:
        return normalize(data)
    return normalize(_from_sillytavern(data))


def _png_card(raw: bytes) -> dict:
    """在 PNG 的 tEXt / iTXt / zTXt 块里找 SillyTavern 的 ccv3 或 chara（base64 编码的 JSON）。不解码图片本身。"""
    found = {}
    pos = 8
    while pos + 8 <= len(raw):
        length, kind = struct.unpack('>I4s', raw[pos:pos+8]); body = raw[pos+8:pos+8+length]; pos += 12+length
        if kind == b'IEND': break
        if kind not in (b'tEXt', b'zTXt', b'iTXt'): continue
        key, _, rest = body.partition(b'\0')
        if key not in (b'chara', b'ccv3'): continue
        try:
            if kind == b'zTXt': rest = zlib.decompress(rest[1:])
            elif kind == b'iTXt':
                compressed, rest = rest[0], rest[2:]
                rest = rest.split(b'\0', 2)[2]
                if compressed: rest = zlib.decompress(rest)
            found[key] = json.loads(base64.b64decode(rest).decode('utf-8'))
        except (ValueError, zlib.error, IndexError, UnicodeDecodeError):
            continue
    data = found.get(b'ccv3') or found.get(b'chara')
    if data is None: raise PersonaError('这张 PNG 里没有角色卡数据')
    return data


_MACROS = re.compile(r'\{\{\s*(char|user)\s*\}\}|<(BOT|USER)>', re.I)


def _macros(text: str) -> str:
    """{{char}} 是她自己 → “你”；{{user}} 是对方 → “对方”（名字属于角色身份，不跟人格走）。"""
    return _MACROS.sub(lambda m: '你' if (m.group(1) or m.group(2) or '').lower() in ('char', 'bot') else '对方', text or '')


def _from_sillytavern(card: dict) -> dict:
    data = card.get('data') if isinstance(card.get('data'), dict) else card
    name = str(data.get('name') or '').strip()

    def text(key):
        value = data.get(key)
        if not isinstance(value, str): return ''
        return _macros(value).strip()

    description = '\n'.join(t for t in (text('description'), text('personality') and '性格：'+text('personality')) if t)
    background = '\n'.join(t for t in (text('scenario'), text('system_prompt')) if t)
    examples = _parse_examples(data.get('mes_example') if isinstance(data.get('mes_example'), str) else '')
    greeting = text('first_mes')
    if greeting: examples.insert(0, {'user': '', 'reply': greeting})
    lore = []
    book = data.get('character_book') if isinstance(data.get('character_book'), dict) else {}
    for entry in book.get('entries') or []:
        if not isinstance(entry, dict) or entry.get('enabled') is False: continue
        keys = [k for k in (entry.get('keys') or []) if isinstance(k, str)]
        content = entry.get('content') if isinstance(entry.get('content'), str) else ''
        lore.append({'keys': keys, 'content': _macros(content),
                     'always': bool(entry.get('constant'))})
    temper = ((data.get('extensions') or {}).get('aicompanion') or {}).get('temper') if isinstance(data.get('extensions'), dict) else None
    return {'title': str(data.get('character_version') or '').strip() or (name+' 的人格' if name else '导入的人格'),
            'description': description, 'speaking_style': '', 'background': background, 'examples': examples,
            'interests': [t for t in (data.get('tags') or []) if isinstance(t, str)], 'lore': lore,
            'temper': temper if isinstance(temper, dict) else {}, 'notes': text('creator_notes')}


def _parse_examples(text: str) -> list:
    """mes_example：以 <START> 分段，每段里 {{user}}: / {{char}}: 开头的行。"""
    examples = []
    for block in re.split(r'<START>', text or '', flags=re.I):
        user, reply = [], []
        current = None
        for line in block.strip().splitlines():
            match = re.match(r'\s*(\{\{\s*(user|char)\s*\}\}|<(USER|BOT)>)\s*[:：]\s*(.*)', line, re.I)
            if match:
                who = (match.group(2) or match.group(3) or '').lower()
                current = user if who == 'user' else reply
                current.append(match.group(4))
            elif current is not None and line.strip():
                current.append(line.strip())
        if reply:
            examples.append({'user': _macros(' '.join(user)), 'reply': _macros(' '.join(reply))})
    return examples
