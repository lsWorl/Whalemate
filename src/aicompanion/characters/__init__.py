"""角色包：一个模型绑定一个角色（人设、台词、立绘、音色、模型供应商）。

纯 Python 实现，不依赖 Qt，便于单元测试，也便于以后安卓端复用。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

PACKS_DIR = Path(__file__).resolve().parent
LIVE2D_ASSETS = PACKS_DIR.parent/'assets/live2d'
RENDERERS = {'sprites', 'none'}
REQUIRED_POSES = ('idle', 'talking', 'thinking')


class CharacterPackError(ValueError):
    pass


def _safe_name(value, what):
    if not isinstance(value, str) or not value or '..' in value or value.startswith(('/', '\\')) or ':' in value or '\\' in value:
        raise CharacterPackError(f'{what} 必须是角色包内的相对路径：{value!r}')
    return value


@dataclass(frozen=True)
class CharacterPack:
    id: str
    name: str
    root: Path
    status: str = 'ready'
    order: int = 100
    theme: str = ''
    renderer: dict = field(default_factory=dict)
    voice: dict = field(default_factory=dict)
    persona: dict | None = None
    lines: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == 'ready'

    @property
    def renderer_kind(self) -> str:
        return self.renderer.get('kind', 'none')

    @property
    def display_height(self) -> int:
        return int(self.renderer.get('display_height', 300))

    def sprite_manifest_path(self) -> Path:
        return self.root/_safe_name(self.renderer.get('path', ''), '立绘配置')

    def line(self, key: str, rng: random.Random | None = None, avoid: str = '') -> str:
        """取一句台词；尽量不与上一句重复。没有该类台词时返回空串。"""
        options = [text for text in self.lines.get(key, []) if isinstance(text, str) and text]
        if not options: return ''
        if len(options) > 1 and avoid in options: options = [text for text in options if text != avoid]
        return (rng or random).choice(options)


def load_pack(directory: Path) -> CharacterPack:
    path = Path(directory)/'character.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise CharacterPackError(f'无法读取 {path}：{error}') from error
    if not isinstance(data, dict) or data.get('format') != 'AICompanionCharacter' or data.get('version') != 1:
        raise CharacterPackError(f'{path} 不是受支持的角色包格式。')
    pack_id, name = data.get('id'), data.get('name')
    if not isinstance(pack_id, str) or not pack_id.replace('-', '').isalnum() or pack_id != Path(directory).name:
        raise CharacterPackError(f'{path} 的 id 必须与目录名一致且只含字母、数字和连字符。')
    if not isinstance(name, str) or not name.strip():
        raise CharacterPackError(f'{path} 缺少角色名。')
    renderer = data.get('renderer') or {'kind': 'none'}
    if renderer.get('kind') not in RENDERERS:
        raise CharacterPackError(f'{path} 的渲染方式 {renderer.get("kind")!r} 不受支持。')
    persona = data.get('persona')
    if persona is not None and not (isinstance(persona, dict) and isinstance(persona.get('description'), str) and isinstance(persona.get('speaking_style'), str)):
        raise CharacterPackError(f'{path} 的人设需要 description 和 speaking_style。')
    lines = {}
    if data.get('lines'):
        lines_path = Path(directory)/_safe_name(data['lines'], '台词文件')
        try:
            lines = json.loads(lines_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            raise CharacterPackError(f'无法读取台词 {lines_path}：{error}') from error
        if not isinstance(lines, dict): raise CharacterPackError(f'{lines_path} 必须是对象。')
    return CharacterPack(id=pack_id, name=name.strip(), root=Path(directory).resolve(), status=data.get('status', 'ready'),
        order=int(data.get('order', 100)), theme=data.get('theme', ''), renderer=renderer,
        voice=data.get('voice') or {}, persona=persona, lines=lines)


def discover(root: Path = PACKS_DIR) -> tuple[list[CharacterPack], list[str]]:
    """扫描角色包目录；损坏的包跳过并返回错误说明，不阻止应用启动。"""
    packs, errors = [], []
    for directory in sorted(Path(root).iterdir()):
        if not (directory/'character.json').is_file(): continue
        try:
            pack = load_pack(directory)
            if pack.ready and pack.renderer_kind == 'sprites': load_sprite_manifest(pack.sprite_manifest_path())
            packs.append(pack)
        except CharacterPackError as error:
            errors.append(str(error))
    packs.sort(key=lambda pack: (pack.order, pack.id))
    return packs, errors


def load_sprite_manifest(path: Path) -> dict:
    """校验立绘配置：格式、尺寸、必需姿势、文件存在且位于同一目录。"""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise CharacterPackError(f'无法读取立绘配置 {path}：{error}') from error
    if data.get('format') != 'AICompanionSpritePack' or data.get('version') != 1:
        raise CharacterPackError(f'{path} 不是受支持的立绘配置。')
    size = data.get('size')
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and 16 <= v <= 4096 for v in size)):
        raise CharacterPackError(f'{path} 的 size 无效。')
    poses = data.get('poses')
    if not isinstance(poses, dict): raise CharacterPackError(f'{path} 缺少 poses。')
    missing = [pose for pose in REQUIRED_POSES if pose not in poses]
    if missing: raise CharacterPackError(f'{path} 缺少必需姿势：{", ".join(missing)}')
    for pose, entry in poses.items():
        for kind in ('base', 'eyes_closed', 'mouth_closed', 'eyes_closed_mouth_closed'):
            if kind not in entry: continue
            name = _safe_name(entry[kind], f'{pose}.{kind}')
            if '/' in name: raise CharacterPackError(f'{pose}.{kind} 必须与 sprites.json 位于同一目录。')
            if not (path.parent/name).is_file(): raise CharacterPackError(f'立绘文件不存在：{name}')
        if 'base' not in entry: raise CharacterPackError(f'姿势 {pose} 缺少 base。')
    return data


def choose_renderer(pack: CharacterPack | None, avatar_mode, model_path: str = '') -> tuple[str, Path | str | None]:
    """决定角色用哪种方式显示，返回 (方式, 资源)。

    方式：'sprites'（原生立绘）、'cubism'（用户导入的 Live2D 模型）、'placeholder'（无可用形象）。
    """
    if avatar_mode == 'live2d' and model_path: return 'cubism', model_path
    if pack and pack.ready and pack.renderer_kind == 'sprites': return 'sprites', pack.sprite_manifest_path()
    if model_path: return 'cubism', model_path
    return 'placeholder', None
