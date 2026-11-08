"""独立的磁盘路径记录：数据库路径不能存到尚未找到的数据库本身。

不读取 .env；所有目录更改都由用户在设置页提交。旧库只复制，失败或重启后仍保留。
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from uuid import uuid4

DATABASE_NAME = 'aicompanion.sqlite3'
DEFAULT_MEMES = {'happy': 'celebrate.png', 'thinking': 'thinking.png', 'sleepy': 'sleepy.png',
                 'surprised': 'poked.png', 'error': 'error.png', 'wait': 'wait.png'}


def project_root() -> Path:
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[3]


def default_data_root(root=None, system_drive=None, available=None) -> Path:
    root = Path(root or project_root()).resolve()
    system = (system_drive or os.getenv('SystemDrive', 'C:')).rstrip('\\/').casefold()
    if os.name != 'nt' or root.drive.casefold() != system: return root/'data'
    # 安装到系统盘也不将记忆和表情默认放进 AppData；优先 D 盘，其次其他可用盘。
    candidates = available if available is not None else [Path(f'{letter}:/') for letter in 'DEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{letter}:/').is_dir()]
    for drive in candidates:
        drive = Path(drive)
        if drive.drive.casefold() != system: return drive/'AICompanion'/'data'
    raise ValueError('未找到非系统磁盘。请用 AICOMPANION_PATHS_FILE 指定可写路径记录，再选择记忆目录。')


def record_path(root=None) -> Path:
    configured = os.getenv('AICOMPANION_PATHS_FILE', '').strip()
    if configured: return Path(configured).expanduser().resolve()
    root = Path(root or project_root()).resolve()
    if os.name == 'nt' and root.drive.casefold() == os.getenv('SystemDrive', 'C:').casefold():
        return default_data_root(root).parent/'.aicompanion-paths.json'
    return root/'.aicompanion-paths.json'


def read_record(path) -> dict:
    try:
        file = Path(path)
        if file.stat().st_size > 64*1024: raise ValueError('路径记录文件过大')
        data = json.loads(file.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict): raise ValueError('路径记录格式错误')
        for key in ('memory_root', 'meme_root', 'database_name'):
            if key in data and not isinstance(data[key], str): raise ValueError('路径记录包含无效目录字段')
        if data.get('migration'):
            migration = data['migration']
            if not isinstance(migration, dict) or any(not isinstance(migration.get(k), str) or not migration[k] for k in ('id', 'source', 'target')):
                raise ValueError('迁移路径记录格式错误')
        return data
    except FileNotFoundError: return {}


def write_record(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.paths-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()


def directory(value) -> Path:
    path = Path(str(value).strip()).expanduser()
    if not str(value).strip() or not path.is_absolute(): raise ValueError('请选择完整的绝对目录路径')
    path = path.resolve()
    if os.name == 'nt' and path.drive.casefold() == os.getenv('SystemDrive', 'C:').casefold():
        raise ValueError('请将记忆和表情目录放在非系统盘，例如 D 盘或 E 盘')
    return path


def writable(path):
    path = Path(path); path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as probe: probe.write(b'AICompanion path check')


def plan_change(database_path, memory_root, meme_root, path_record, persist=True):
    """只保存下一次启动的计划，不在仍有后台写入的会话中复制数据库。"""
    memory, memes = directory(memory_root), directory(meme_root)
    current = Path(database_path).resolve(); destination = memory/current.name
    before = read_record(path_record)
    if destination != current and destination.exists():
        raise ValueError('目标目录已有 aicompanion.sqlite3，为避免覆盖已有记忆，请选择空目录')
    writable(memory); writable(memes)
    result = dict(before, memory_root=str(memory), meme_root=str(memes), database_name=current.name)
    if destination != current:
        result['migration'] = {'id': uuid4().hex, 'source': str(current), 'target': str(destination)}
    else:
        result.pop('migration', None)
    if persist: write_record(path_record, result)
    return result


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024*1024), b''): digest.update(chunk)
    return digest.hexdigest()


def copy_database(source, target, migration_id):
    """SQLite 在线 backup，校验后一次发布。目标不覆盖；原库及其日志从不移动或删除。"""
    source, target = Path(source).resolve(), Path(target).resolve()
    import re
    if not re.fullmatch(r'[0-9a-f]{32}', migration_id): raise ValueError('迁移记录的编号无效')
    if source == target: return
    marker = target.parent/f'.migration-{migration_id}.json'
    if target.exists():
        completed = read_record(marker)
        if completed.get('id') == migration_id and completed.get('sha256') == _digest(target): return
        raise ValueError('目标数据库已存在，未覆盖；旧数据库仍保留')
    if not source.is_file(): raise ValueError('原数据库不存在，已取消迁移，未创建空记忆库')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.memory-', suffix='.sqlite3', dir=target.parent); os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=10)) as old:
            with closing(sqlite3.connect(temporary)) as new:
                old.backup(new)
                if new.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('新数据库完整性检查失败')
        # 记录快照校验值以恢复“已复制但未写回路径记录”的中断；不包含聊天内容。
        write_record(marker, {'id': migration_id, 'sha256': _digest(temporary)})
        if os.name == 'nt':
            os.rename(temporary, target)   # Windows rename 对已存在目标会报错，不覆盖
        else:
            os.link(temporary, target)     # POSIX 同样原子地拒绝已存在目标
            Path(temporary).unlink()
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()


def seed_memes(destination):
    """新图库补入仓库自带原创表情；已有文件和用户图库都不覆盖。"""
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1]/'characters'/'deepseek-whale'/'sprites'
    if any(destination.iterdir()): return
    for category, filename in DEFAULT_MEMES.items():
        if not (source/filename).is_file(): continue
        folder = destination/category; folder.mkdir(exist_ok=True)
        target = folder/filename
        try:
            with target.open('xb') as out, (source/filename).open('rb') as image: shutil.copyfileobj(image, out)
        except FileExistsError: pass


def prepare(settings):
    """仅正式启动、单实例检查通过后调用；自检模式不迁移或读取用户数据。"""
    config = read_record(settings.storage_paths_file)
    migration = config.get('migration') or {}
    source = migration.get('source') or (settings.migration_source if not config.get('memory_root') else None)
    if source and Path(source).resolve() != Path(settings.database_path).resolve():
        if migration and Path(migration['target']).resolve() != Path(settings.database_path).resolve():
            raise ValueError('待迁移目标与当前数据库路径不一致，已取消启动')
        if not migration:
            migration = {'id': uuid4().hex, 'source': str(source), 'target': str(settings.database_path)}
            config.update(memory_root=str(Path(settings.database_path).parent), meme_root=str(settings.meme_directory),
                          database_name=Path(settings.database_path).name, migration=migration)
            write_record(settings.storage_paths_file, config)
        copy_database(source, settings.database_path, migration['id'])
        config.pop('migration', None); write_record(settings.storage_paths_file, config)
    seed_memes(settings.meme_directory)
