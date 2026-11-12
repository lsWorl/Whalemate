"""在一次性构建目录里生成 Windows x64 发布包及校验文件。"""
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

# 源码运行构建工具时也使用随应用打包的清单格式。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from aicompanion.release_manifest import EXE, MANIFEST, inventory

if __package__:
    from .build_windows import character_data_args
else:
    from build_windows import character_data_args

ROOT = Path(__file__).resolve().parents[1]
UNINSTALLER = Path(EXE).stem
PUBLIC = ('LICENSE', 'README.md', 'README.en.md', '.env.example',
          'docs/aboutme-pet.md', 'docs/live2d_model_sources.md', 'docs/storage-locations.md',
          'tools/install_aboutme_pet.py', 'tools/aboutme-pet.lock.json', 'tools/aboutme-requirements.txt')


def check_member(relative):
    """同时检查文件和所有祖先。Aboutme 安装器和来源说明是允许分发的文本。"""
    if not str(relative).isascii():
        raise ValueError(f'发布目录和文件须使用英文命名：{relative}')
    path = PurePosixPath(str(relative).replace('\\', '/').lower())
    for part in path.parts:
        if (part in {'data', 'logs', 'log', '__pycache__', 'coopanion', 'aboutme-cache', '.aicompanion-paths.json'}
                or part.startswith(('animation-', 'private-'))
                or (part.startswith('.env') and str(path) != '.env.example')
                or re.search(r'\.(?:sqlite3?|db)(?:$|[-.])', part)
                or part.endswith(('.log', '.webp', '.moc3', '.pfx', '.pem.key'))
                or 'cubismcore' in part or 'cubism_core' in part
                or ('aboutme' in part and Path(part).suffix in {'.png', '.gif', '.jpg', '.jpeg', '.zip'})):
            raise ValueError(f'发布包混入禁止分发的文件：{relative}')


def stage_assets(work):
    # 仅取 Git 管理的应用资源；开发机后来下载的 Core、缓存和私人素材不会成为输入。
    archive = work/'public.tar'
    subprocess.run(['git', '-c', f'safe.directory={ROOT.as_posix()}', 'archive', '-o', str(archive), 'HEAD',
                    'src/aicompanion/assets', 'src/aicompanion/characters', 'integrations/astrbot_plugin_aicompanion'],
                   check=True, cwd=ROOT)
    sources = work/'sources'
    with tarfile.open(archive) as files:
        for member in files:
            check_member(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f'仓库资源含链接或特殊文件：{member.name}')
            files.extract(member, sources, filter='data')
    (sources/'src/aicompanion/assets').rename(work/'assets')
    (sources/'integrations').rename(work/'integrations')


def version_info(path, version, name):
    numbers = tuple(map(int, version.split('.')))
    quad = (numbers + (0, 0, 0, 0))[:4]
    description = 'Whalemate Uninstaller' if name == UNINSTALLER else 'Whalemate'
    fields = {'FileDescription': description, 'ProductName': 'Whalemate',
              'InternalName': name, 'OriginalFilename': name+'.exe', 'FileVersion': version, 'ProductVersion': version}
    entries = ','.join(f'StringStruct({key!r},{value!r})' for key, value in fields.items())
    path.write_text(f"VSVersionInfo(ffi=FixedFileInfo(filevers={quad},prodvers={quad},mask=63,flags=0,"
                    f"OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable("
                    f"'080404b0',[{entries}])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])", encoding='utf-8')


def freeze(work, name, entry, version, options):
    resource = work/(name+'-version.txt')
    version_info(resource, version, name)
    # 排除构建工具附带的同名 ICU。尤其是 onefile，收进 exe 后无法再从目录剔除。
    system32 = Path(os.environ['SystemRoot'])/'System32'
    clean_path = [p for p in os.get_exec_path() if p and (Path(p) == system32 or not (Path(p)/'icuuc.dll').is_file())]
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--noupx',
                    '--name', name, '--icon', str(work/'assets/icons/aicompanion.ico'),
                    '--version-file', str(resource), '--paths', str(ROOT/'src'),
                    '--distpath', str(work/'dist'), '--workpath', str(work/'build'), '--specpath', str(work),
                    *options, str(ROOT/entry)], cwd=work, check=True,
                   env=dict(os.environ, PATH=os.pathsep.join(clean_path), PYINSTALLER_CONFIG_DIR=str(work/'pyinstaller-cache')))


def validated_members(bundle, required_files):
    members = sorted(bundle.rglob('*'))
    for required in required_files:
        if not (bundle/required).is_file():
            raise ValueError(f'发布包缺少 {required}')
    for item in members:
        relative = item.relative_to(bundle)
        check_member(relative)
        if (item.is_file() and relative.parts[0] != '_internal'
                and relative.as_posix() not in (*PUBLIC, 'AICompanion.exe', UNINSTALLER+'.exe')):
            raise ValueError(f'发布包出现未列入清单的文件：{relative}')
        if item.resolve() != item.absolute():
            raise ValueError(f'发布目录含外部链接：{item}')
        if item.is_file():
            with item.open('rb') as stream:
                if stream.read(16) == b'SQLite format 3\0':
                    raise ValueError(f'发布目录含 SQLite 数据库：{relative}')
    return members


def package(work, bundle, release, version):
    members = validated_members(bundle, (*PUBLIC, 'AICompanion.exe', EXE, MANIFEST))
    if (bundle/MANIFEST).read_bytes() != inventory(bundle):
        raise ValueError('发布内容与冻结卸载程序时生成的清单不一致')
    name = f'Whalemate-v{version}-windows-x64.zip'
    archive = work/name
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for item in members:
            output.write(item, (Path('Whalemate')/item.relative_to(bundle)).as_posix())
    with archive.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    sums = work/'SHA256SUMS.txt'
    sums.write_text(f'{digest}  {name}\n', encoding='ascii')
    release.mkdir(parents=True, exist_ok=True)
    # 输出可位于不同的卷，先写同级临时文件再替换，失败不会留下半个 zip。
    with tempfile.TemporaryDirectory(dir=release) as transfer:
        for source in (archive, sums):
            staged = Path(transfer)/source.name
            shutil.copyfile(source, staged)
            staged.replace(release/source.name)
    print(f'{release/name}\n{archive.stat().st_size} bytes\nSHA256 {digest}', flush=True)


def main():
    if sys.platform != 'win32' or platform.machine().upper() not in ('AMD64', 'X86_64') or sys.maxsize < 2**32:
        raise SystemExit('请在 Windows x64 和 64 位 Python 环境构建')
    version = tomllib.loads((ROOT/'pyproject.toml').read_text('utf-8'))['project']['version']
    with tempfile.TemporaryDirectory(prefix='whalemate-release-') as tmp:
        work = Path(tmp)
        stage_assets(work)
        freeze(work, 'AICompanion', 'main.py', version, [
            '--add-data', f'{work/"assets"}:aicompanion/assets',
            '--add-data', f'{work/"integrations"}:aicompanion/integrations',
            *character_data_args(work/'sources', work/'art'), '--additional-hooks-dir', str(ROOT/'tools/hooks'),
            '--collect-data', 'deepseek_harness_runtime', '--hidden-import', '_webrtcvad',
            '--collect-submodules', 'uvicorn', '--hidden-import', 'anyio._backends._asyncio',
        ])
        bundle = work/'dist/AICompanion'
        # 唯一允许剔除的是构建机 PATH 里的冲突 ICU DLL，Qt 在 Windows 上使用系统 ICU。
        for name in ('icuuc.dll', 'icudt78.dll'):
            (bundle/'_internal'/name).unlink(missing_ok=True)
        for name in PUBLIC:
            (bundle/name).parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT/name, bundle/name)
        validated_members(bundle, (*PUBLIC, 'AICompanion.exe'))
        manifest = bundle/MANIFEST
        manifest.write_bytes(inventory(bundle))
        freeze(work, UNINSTALLER, 'tools/uninstall_entry.py', version, [
            '--onefile', '--add-data', f'{manifest}:.', '--add-data', f'{work/"assets/icons"}:aicompanion/assets/icons'])
        (work/'dist'/EXE).rename(bundle/EXE)
        package(work, bundle, ROOT/'dist/release', version)


if __name__ == '__main__':
    main()
