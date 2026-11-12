"""鲸伴卸载。扫描生成逐项清单，执行器只接受经过确认的同一份快照。"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile

from aicompanion.qq.layout import Layout
from aicompanion.qq.supervisor import Supervisor, process_identity
from aicompanion.storage.locations import DATABASE_NAME, default_data_root, read_record
from aicompanion.ui.desktop_integration import RUN_KEY, RUN_NAME

EXE = '卸载鲸伴.exe'
# 存储文档使用同一组名称；每项的实际路径、文件大小另外列出。
PROGRAM = '程序、运行配置与本机安装的角色资源'
MEMORY = '记忆数据库、备份、迁移记录和数据库内的 DPAPI 凭据'
RECORD = '存储位置记录'
MEMES = '默认表情图库'
EXTERNAL = '用户选择的外部表情图库'
LOCAL = '旧数据库、语音缓存、识别模型、角色缓存和日志'
LOW = '办事沙箱和 DSH 会话'
QQ = 'QQ 组件和运行环境'
TRASH = '办事回收区和旧版快照'
STARTUP = '当前用户的鲸伴开机启动项'
CATEGORIES = (PROGRAM, MEMORY, RECORD, MEMES, EXTERNAL, LOCAL, LOW, QQ, TRASH, STARTUP)


@dataclass
class Locations:
    install: Path
    default: Path
    local: Path
    home: Path
    drives: tuple[Path, ...]
    run_key: str = RUN_KEY
    fence: Path | None = None

    @classmethod
    def current(cls, install):
        home = Path(os.environ['USERPROFILE'])
        local = Path(os.environ['LOCALAPPDATA'])/'AICompanion'
        # 打包验收可缩小可访问范围；启动项也固定重定向到测试命名空间。
        sandbox = os.getenv('WHALEMATE_UNINSTALL_TEST_ROOT')
        if sandbox:
            fence = Path(sandbox).resolve(strict=True)
            if (not re.fullmatch(r'whalemate-test-[a-f0-9]{32}', fence.name)
                    or fence.parent != Path(tempfile.gettempdir()).resolve()
                    or not install.resolve().is_relative_to(fence)):
                raise ValueError('无效的卸载测试隔离目录')
            return cls(install, fence/'disk/AICompanion/data', local, home, (fence/'disk',),
                       'Software\\Whalemate-Uninstall-Tests\\'+fence.name, fence)
        try:
            default = default_data_root(install)
        except ValueError:
            # 只有系统盘时也能卸载尚未完成初始化的解压包。
            default = install/'data'
        return cls(install, default, local, home, tuple(map(Path, os.listdrives())))

    def check(self, path):
        path = Path(path)
        if (not path.is_absolute() or '..' in path.parts or path.drive.startswith('\\\\')
                or any(':' in p or p.endswith((' ', '.')) or os.path.isreserved(p) for p in path.parts[1:])):
            raise ValueError(f'拒绝不安全的路径：{path}')
        resolved = path.resolve()
        if path != resolved or (self.fence and not resolved.is_relative_to(self.fence)):
            raise ValueError(f'拒绝链接、联接点或越界路径：{path}')
        profile_roots = (self.home, self.home/'AppData', self.local.parent,
                         self.home/'AppData/LocalLow', self.home/'AppData/Roaming')
        if path == Path(path.anchor) or any(folder.is_relative_to(path) for folder in profile_roots):
            raise ValueError(f'拒绝磁盘根目录或用户目录：{path}')
        for key in ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData'):
            folder = os.environ.get(key)
            if folder and (path.is_relative_to(folder) or Path(folder).is_relative_to(path)):
                raise ValueError(f'拒绝系统目录：{path}')
        return path


@dataclass(frozen=True)
class Item:
    path: Path
    category: str
    directory: bool
    size: int
    identity: tuple[int, int]


@dataclass
class Plan:
    locations: Locations
    items: dict[Path, Item] = field(default_factory=dict)
    galleries: set[Path] = field(default_factory=set)
    external_memory: set[Path] = field(default_factory=set)
    qq: set[Path] = field(default_factory=set)
    startup: tuple | None = None
    notes: list[str] = field(default_factory=list)

    def add(self, path, category, *, tree=False, empty_after=False):
        path = self.locations.check(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'拒绝重解析点：{path}')
        directory = stat.S_ISDIR(info.st_mode)
        if empty_after and (not directory or any(child not in self.items for child in path.iterdir())):
            return
        if directory and not (tree or empty_after):
            raise ValueError(f'预期为文件，却找到目录：{path}')
        # 先登记的专用类别保留，外部图库在整个清单建好后单独过滤。
        if path in self.items:
            return
        self.items[path] = Item(path, category, directory, 0 if directory else info.st_size, (info.st_dev, info.st_ino))
        if directory and tree:
            for child in sorted(path.iterdir()):
                self.add(child, category, tree=True)

    def selected(self, galleries=False):
        def wanted(item):
            path = item.path
            if not galleries and any(path.is_relative_to(g) or g.is_relative_to(path) for g in self.galleries):
                return False
            # 外部记忆位置即使嵌在旧缓存或程序目录里，也只移除可核实的专属文件。
            if item.category not in (MEMORY, RECORD, MEMES, EXTERNAL):
                if any(path.is_relative_to(m) or m.is_relative_to(path) for m in self.external_memory):
                    return False
            return True
        return sorted((item for item in self.items.values() if wanted(item)),
                      key=lambda item: (item.path.is_relative_to(self.locations.install), -len(item.path.parts), str(item.path)))

    @property
    def startup_path(self):
        return 'HKCU\\'+self.locations.run_key+'\\'+RUN_NAME

    def lines(self, galleries=False):
        selected = {item.path for item in self.selected(galleries)}
        for item in self.items.values():
            action = '删除' if item.path in selected else '保留'
            yield f'{action}\t{item.size} B\t{item.category}\t{item.path}'
        for note in self.notes:
            yield '提示\t'+note
        if self.startup:
            yield f'删除\t{len(self.startup[0].encode("utf-16-le"))} B\t{STARTUP}\t{self.startup_path}'


def registry_value(locations):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, locations.run_key) as key:
            return winreg.QueryValueEx(key, RUN_NAME)
    except FileNotFoundError:
        return None


def media_settings(database):
    """只查询目录设置。WAL 库在临时副本上读取，预览不会创建或修改原库的 sidecar。"""
    def read(path, query):
        with closing(sqlite3.connect(path.as_uri()+query, uri=True)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('app_settings','chat_messages')")}
            if 'app_settings' not in tables:
                return {} if 'chat_messages' in tables else None
            rows = conn.execute("SELECT key,value FROM app_settings WHERE key IN ('qq_root','qq_meme_root','blog_root')")
            return {key: json.loads(value) for key, value in rows}
    if not database.is_file():
        return {}
    wal = Path(str(database)+'-wal')
    if not wal.is_file():
        return read(database, '?mode=ro&immutable=1')
    with tempfile.TemporaryDirectory(prefix='whalemate-paths-') as tmp:
        target = Path(tmp)/'settings.sqlite3'
        shutil.copyfile(database, target)
        shutil.copyfile(wal, str(target)+'-wal')
        return read(target, '?mode=ro')


def database_files(plan, path):
    path = plan.locations.check(path)
    if path.suffix.casefold() not in ('.sqlite3', '.sqlite', '.db'):
        raise ValueError(f'无法核实数据库文件名：{path}')
    backups = re.compile(re.escape(path.stem)+r'\.backup-[\w-]+\.sqlite3(?:-(?:wal|shm|journal))?', re.I)
    if path.parent.is_dir():
        for entry in path.parent.iterdir():
            if (entry.name.casefold() in {path.name.casefold()+suffix for suffix in ('', '-wal', '-shm', '-journal')}
                    or backups.fullmatch(entry.name) or re.fullmatch(r'\.migration-[a-f0-9]{32}\.json', entry.name)):
                plan.add(entry, MEMORY)


def scan(locations):
    install = locations.check(locations.install)
    if not (install/'AICompanion.exe').is_file() or not (install/'_internal').is_dir() or not (install/EXE).is_file():
        raise ValueError(f'这里不是完整的鲸伴程序目录：{install}')
    plan = Plan(locations)
    default = locations.check(locations.default)
    local = locations.check(locations.local)
    # 旧安装可在 .env 里指定路径；只取这两个路径键，绝不显示凭据。
    from dotenv import dotenv_values
    dotenv = dotenv_values(locations.check(install/'.env'), interpolate=False)
    record_override = os.getenv('AICOMPANION_PATHS_FILE') or dotenv.get('AICOMPANION_PATHS_FILE')
    records = {install/'.aicompanion-paths.json', default.parent/'.aicompanion-paths.json'}
    if record_override:
        records.add(Path(record_override))
    databases = {default/DATABASE_NAME, local/'data'/DATABASE_NAME}
    old_database = os.getenv('DATABASE_PATH') or dotenv.get('DATABASE_PATH')
    if old_database:
        old = install/old_database
        databases.add(old)
        if old.drive.casefold() == os.getenv('SystemDrive', 'C:').casefold():
            databases.add(default/old.name)
    default_galleries = {default/'memes'}
    default_qq = local/'qq'
    plan.qq.add(default_qq)
    for record in sorted(records):
        locations.check(record)
        if not record.exists():
            continue
        config = read_record(record)
        if record.name != '.aicompanion-paths.json' and not config.get('memory_root') and not config.get('meme_root'):
            raise ValueError(f'指定文件不是鲸伴路径记录：{record}')
        plan.add(record, RECORD)
        memory = locations.check(Path(config.get('memory_root') or default))
        if memory != default and memory != local/'data':
            if install.is_relative_to(memory) or local.is_relative_to(memory):
                raise ValueError(f'记忆目录覆盖程序或应用数据目录：{memory}')
            plan.external_memory.add(memory)
        name = config.get('database_name', DATABASE_NAME)
        if Path(name).name != name or not name:
            raise ValueError('路径记录含不安全的数据库文件名')
        databases.add(memory/name)
        if config.get('meme_root'):
            plan.galleries.add(locations.check(config['meme_root']))
        else:
            default_galleries.add(memory/'memes')
        for key in ('source', 'target'):
            if value := config.get('migration', {}).get(key):
                databases.add(Path(value))
    for database in sorted(databases):
        locations.check(database.parent)
        locations.check(database)
        locations.check(Path(str(database)+'-wal'))
        try:
            settings = media_settings(database)
        except (OSError, sqlite3.DatabaseError, json.JSONDecodeError) as error:
            plan.notes.append(f'{database} 中的外部目录设置无法读取（{type(error).__name__}）。未核实的外部目录保留。')
            settings = None
        if settings is None and database.name.casefold() != DATABASE_NAME:
            plan.notes.append(f'保留无法核实归属的自定义数据库及其备份：{database}')
            continue
        database_files(plan, database)
        settings = settings or {}
        if settings.get('qq_root'):
            plan.qq.add(locations.check(settings['qq_root']))
        if settings.get('qq_meme_root'):
            plan.galleries.add(locations.check(settings['qq_meme_root']))
        if settings.get('blog_root'):
            plan.add(Path(settings['blog_root'])/'.aicompanion-trash', TRASH, tree=True)
    plan.galleries.difference_update(default_galleries)
    for gallery in plan.galleries.copy():
        if install.is_relative_to(gallery) or local.is_relative_to(gallery):
            raise ValueError(f'图库覆盖程序或应用数据目录：{gallery}')
        if gallery.exists():
            if not gallery.is_dir():
                raise ValueError(f'图库不是目录：{gallery}')
            plan.add(gallery, EXTERNAL, tree=True)
        else:
            plan.galleries.remove(gallery)
    for gallery in sorted(default_galleries):
        plan.add(gallery, MEMES, tree=True)
    for root in sorted(plan.qq):
        if root == default_qq:
            plan.add(root, QQ, tree=True)
        elif root.is_dir():
            # 高级设置可能指到一个已有目录。只认带安装记录的 QQ 布局。
            manifest = Layout(root).manifest()
            if not {'components', 'processes'}.intersection(manifest):
                raise ValueError(f'无法核实外部 QQ 组件安装记录：{root}')
            for name in ('manifest.json', 'downloads', 'napcat', 'uv', 'astrbot', 'python', 'uv-cache',
                         'astrbot-home', 'napcat-home', 'qq-data', 'logs'):
                plan.add(root/name, QQ, tree=name != 'manifest.json')
    for drive in locations.drives:
        for name in ('.aicompanion-trash', '.aicompanion-snapshots'):
            # 没有挂载介质的盘符不参与枚举。
            if (drive/name).is_dir():
                plan.add(drive/name, TRASH, tree=True)
    plan.add(default, MEMORY, empty_after=True)
    if default.parent != install and default.parent.name.casefold() == 'aicompanion':
        plan.add(default.parent, RECORD, empty_after=True)
    plan.add(local, LOCAL, tree=True)
    plan.add(locations.home/'AppData/LocalLow/AICompanion', LOW, tree=True)
    plan.add(install, PROGRAM, tree=True)
    startup = registry_value(locations)
    if startup and isinstance(startup[0], str) and Path(startup[0].strip('"')) == install/'AICompanion.exe':
        plan.startup = startup
    return plan


class UninstallSupervisor(Supervisor):
    def _forget(self, name):
        # 普通 stop 会替换 manifest；卸载要保留预览时那个文件的身份，稍后按清单删除。
        pass


def ensure_stopped(plan):
    """桌宠可能正在写库，无论从哪一份安装启动，都先让用户从托盘退出。"""
    import csv
    import subprocess
    command = Path(os.environ['SystemRoot'])/'System32/tasklist.exe'
    result = subprocess.run([str(command), '/FI', 'IMAGENAME eq AICompanion.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    for row in csv.reader(result.stdout.decode('mbcs', 'replace').splitlines()):
        if not row or row[0].casefold() != 'aicompanion.exe':
            continue
        if plan.locations.fence:
            identity = process_identity(int(row[1]))
            if not identity or not Path(identity[0]).is_relative_to(plan.locations.fence):
                continue
        raise ValueError('鲸伴还在运行。请从托盘退出鲸伴，等它关闭后再点卸载。')
    for root in plan.qq:
        supervisor = UninstallSupervisor(Layout(root))
        for name in ('astrbot', 'napcat'):
            if supervisor.running(name) and not supervisor.stop(name):
                raise ValueError(f'请先退出仍在运行的 QQ 组件：{name}')


def validate_item(plan, item):
    path = plan.locations.check(item.path)
    info = path.lstat()
    if ((info.st_dev, info.st_ino) != item.identity or item.directory != stat.S_ISDIR(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError(f'预览后路径已被替换，请重新预览：{path}')


def execute(plan, *, confirmed, galleries=False, progress=lambda *args: None):
    if not confirmed:
        raise ValueError('请先勾选“我知道删除后无法恢复”')
    selected = plan.selected(galleries)
    for marker in ('AICompanion.exe', '_internal', EXE):
        validate_item(plan, plan.items[plan.locations.install/marker])
    for item in selected:
        try:
            validate_item(plan, item)
        except OSError:
            # 文件暂时被占用或已经消失，继续全局安全检查，在逐项执行时报告。
            pass
    ensure_stopped(plan)
    succeeded, failed = [], []
    if plan.startup:
        import winreg
        try:
            if registry_value(plan.locations) != plan.startup:
                raise ValueError('启动项已变化')
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, plan.locations.run_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_NAME)
            succeeded.append(plan.startup_path)
        except (OSError, ValueError) as error:
            failed.append(f'{plan.startup_path}：{error}')
    for i, item in enumerate(selected, 1):
        try:
            validate_item(plan, item)
            if item.directory:
                item.path.rmdir()   # 新出现的文件不会被递归带走。
            else:
                item.path.unlink()
            succeeded.append(str(item.path))
        except (OSError, ValueError) as error:
            failed.append(f'{item.path}：{error}')
        progress(i, len(selected), item.path)
    return succeeded, failed


if __name__ == '__main__':
    from aicompanion.uninstall_ui import main
    raise SystemExit(main())
