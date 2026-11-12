"""发布清单同时放在安装目录和独立卸载程序内部。两个副本须逐字节一致。"""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import sys

EXE = 'UninstallWhalemate.exe'
MANIFEST = '_internal/whalemate-files.json'


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def trusted_inventory():
    if getattr(sys, 'frozen', False):
        return (Path(sys._MEIPASS)/'whalemate-files.json').read_bytes()
    # 开发者从源码调用时，用本 checkout 构建的 zip 核验，不运行目标目录中的 exe。
    import tomllib
    import zipfile
    checkout = Path(__file__).resolve().parents[2]
    version = tomllib.loads((checkout/'pyproject.toml').read_text('utf-8'))['project']['version']
    archive = checkout/'dist/release'/f'Whalemate-v{version}-windows-x64.zip'
    if not archive.is_file():
        raise ValueError('请先从源码构建对应版本的发布包，或运行安装目录中的卸载程序。')
    with zipfile.ZipFile(archive) as bundle:
        return bundle.read('Whalemate/'+MANIFEST)


def inventory(bundle):
    files = {EXE: None, MANIFEST: None}
    directories = []
    for path in sorted(bundle.rglob('*')):
        relative = path.relative_to(bundle).as_posix()
        if path.is_dir():
            directories.append(relative)
        elif relative not in (MANIFEST, EXE):
            files[relative] = {'size': path.stat().st_size, 'sha256': file_digest(path)}
    # 这两个文件参与清单自身的构建，大小在实际卸载预览时读取。
    return (json.dumps({'version': 1, 'files': files, 'directories': directories},
                       sort_keys=True, indent=1)+'\n').encode('ascii')


def read_inventory(root, trusted):
    path = root/MANIFEST
    if not path.is_file():
        raise ValueError('缺少发布文件清单，无法核实安装内容。请用同版本的完整发布包恢复清单。')
    if trusted is None or path.stat().st_size != len(trusted) or path.read_bytes() != trusted:
        raise ValueError('发布文件清单与卸载程序不匹配，请恢复同版本的完整发布包。')
    data = json.loads(trusted)
    files, dirs = data['files'], data['directories']
    if data['version'] != 1 or not {EXE, MANIFEST, 'AICompanion.exe'} <= files.keys() or '_internal' not in dirs:
        raise ValueError('发布文件清单缺少程序条目')
    for name in (*files, *dirs):
        rel = PureWindowsPath(name)
        if (not rel.parts or rel.anchor or '..' in rel.parts or rel.as_posix() != name
                or any(':' in part for part in rel.parts)):
            raise ValueError(f'发布文件清单含非相对路径：{name}')
    return {root/name: record for name, record in files.items()}, {root/name for name in dirs}
