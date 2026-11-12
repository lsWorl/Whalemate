"""Windows 发布包验收：只使用一次性用户目录、测试注册表子键和模拟文件。"""
import argparse
from contextlib import closing, nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from uuid import uuid4
import zipfile

from build_release import check_member

UNINSTALLER = 'UninstallWhalemate.exe'
ROOT = Path(__file__).resolve().parents[1]


def expect(condition, message):
    if not condition:
        raise RuntimeError(message)


def extract(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = Path(member.filename).parts
            if not parts or parts[0] != 'Whalemate' or '..' in parts or not (target/member.filename).resolve().is_relative_to(target):
                raise ValueError(f'非法成员路径：{member.filename}')
            check_member(Path(*parts[1:]))
        bundle.extractall(target)
    install = target/'Whalemate'
    for exe in ('AICompanion.exe', UNINSTALLER):
        expect((install/exe).is_file(), '缺少 '+exe)
    # pefile 为 Windows PyInstaller 的现有依赖。读 PE 资源核对实际 exe 的子系统、图标和版本。
    import pefile
    for executable in (install/'AICompanion.exe', install/UNINSTALLER):
        with pefile.PE(str(executable)) as pe:
            expect(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Subsystem == 2, '不是 x64 GUI 程序')
            ids = {entry.id for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
            expect({3, 14, 16}.issubset(ids), '缺少图标或版本资源')
            metadata = next(info.StringTable[0].entries for block in pe.FileInfo for info in block
                            if info.Key == b'StringFileInfo')
            description = 'Whalemate Uninstaller' if executable.name == UNINSTALLER else 'Whalemate'
            expect(metadata[b'FileDescription'].decode() == description, '文件说明不符')
            expect(metadata[b'ProductName'] == b'Whalemate' and
                   metadata[b'OriginalFilename'].decode() == executable.name, '产品名或原始文件名不符')
            version = metadata[b'ProductVersion'].decode()
            expect(archive.name == f'Whalemate-v{version}-windows-x64.zip', '版本与包名不符')
    return install


def environment(root):
    env = dict(os.environ, USERPROFILE=str(root/'profile'), LOCALAPPDATA=str(root/'profile/AppData/Local'),
               APPDATA=str(root/'profile/AppData/Roaming'), SystemDrive='Z:', AICOMPANION_OFFLINE='1',
               AICOMPANION_PATHS_FILE=str(root/'paths/record.json'), WHALEMATE_UNINSTALL_TEST_ROOT=str(root))
    for key in ('DATABASE_PATH', 'AICOMPANION_RIG', 'AICOMPANION_LIVE2D_MODEL'):
        env.pop(key, None)
    for key in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA'):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def smoke(install, root, env, flag, report_dir):
    proc = subprocess.run([str(install/'AICompanion.exe'), flag], cwd=root, env=env, timeout=150)
    report = root/report_dir/'report.json'
    expect(report.is_file(), f'{flag} 没有生成报告，退出码 {proc.returncode}')
    result = json.loads(report.read_text('utf-8'))
    expect(proc.returncode == 0 and result.get('passed'),
           f'{flag} 退出码 {proc.returncode}，失败检查 {result.get("failed_checks")}，错误 {result.get("errors")}，消息角色 {result.get("roles")}')
    print(flag, 'passed', flush=True)


def used_machine(root, install, env):
    import winreg
    memory = root/'my-memory'
    memory.mkdir()
    database = memory/'aicompanion.sqlite3'
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute('CREATE TABLE app_settings(key,value)')
        conn.execute('INSERT INTO app_settings VALUES (?,?)', ('qq_meme_root', json.dumps(str(root/'my-gallery'))))
    record = Path(env['AICOMPANION_PATHS_FILE'])
    record.parent.mkdir()
    record.write_text(json.dumps({'memory_root':str(memory), 'meme_root':str(root/'my-gallery')}), 'utf-8')
    local = Path(env['LOCALAPPDATA'])/'AICompanion'
    files = [memory/'aicompanion.backup-test.sqlite3', memory/'aicompanion.sqlite3-shm',
             memory/('.migration-'+'a'*32+'.json'), root/'disk/AICompanion/data/memes/默认.png',
             root/'disk/AICompanion/.aicompanion-paths.json', local/'voice-cache/demo.wav', local/'models/asr/test',
             local/'qq/napcat/test', local/'qq/astrbot/test', local/'rigs/test', local/'logs/app.log',
             Path(env['USERPROFILE'])/'AppData/LocalLow/AICompanion/dsh/session',
             root/'disk/.aicompanion-trash/one/fixture', install/'.env',
             install/'_internal/aicompanion/characters/deepseek-whale/animation-fixture/synthetic.txt']
    # SQLite 完成 checkpoint 后也可能保留零长度的 WAL。
    wal = Path(str(database)+'-wal')
    wal.touch()
    keep = [root/'my-gallery/keep.png', memory/'other.sqlite3', root/'unrelated/keep.txt']
    for path in files+keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}' if path.suffix == '.json' else 'fixture', 'utf-8')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, test_key(root)) as key:
        winreg.SetValueEx(key, 'AICompanion', 0, winreg.REG_SZ, f'"{install/"AICompanion.exe"}"')
        winreg.SetValueEx(key, 'Unrelated', 0, winreg.REG_SZ, 'keep')
    return [database, record, *files, wal], keep


def test_key(root):
    return 'Software\\Whalemate-Uninstall-Tests\\'+root.name


def uninstall_case(root, install, env, scenario):
    import winreg
    locked = scenario == 'locked'
    deleted, keep = used_machine(root, install, env)
    result = subprocess.run([str(install/UNINSTALLER), '--dry-run'], capture_output=True,
                            cwd=root, env=env, timeout=90)
    preview = result.stdout.decode('utf-8-sig')
    expect(result.returncode == 0, preview[-1000:])
    lines = preview.splitlines()
    planned = {line.split('\t')[-1] for line in lines if line.startswith('删除\t')}
    expected = [*deleted, install, install/UNINSTALLER, install/'AICompanion.exe']
    for path in expected:
        expect(str(path) in planned and path.exists(), f'预览遗漏或改动了 {path}')
    expect('HKCU\\'+test_key(root)+'\\AICompanion' in planned, '预览遗漏测试启动项')
    expect(all(str(path) not in planned for path in keep), '预览误选无关文件')
    expect(any(line.startswith('保留\t') and line.endswith(str(keep[0])) for line in lines), '预览遗漏外部图库')
    print(f'--dry-run 已核对 {len(planned)} 项', flush=True)
    busy = deleted[2]
    shell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    with busy.open('rb') if locked else nullcontext():
        subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned',
                        '-File', str(ROOT/'tools/verify_uninstall_ui.ps1'), '-Install', str(install),
                        '-Scenario', scenario], env=env, cwd=root, check=True, timeout=240)
    for entry in planned:
        if entry.startswith('HKCU\\'):
            continue
        path = Path(entry)
        if locked and (path == busy or path in busy.parents):
            continue
        expect(not path.exists(), f'卸载残留：{path}')
    expect(all(p.read_bytes() == b'fixture' for p in keep), '外部图库或其他无关文件被改动')
    expect(busy.exists() == locked, '被占用文件结果错误')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key(root)) as key:
        expect(winreg.QueryValueEx(key, 'Unrelated')[0] == 'keep', '无关注册表值被改动')
        try:
            winreg.QueryValueEx(key, 'AICompanion')
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError('测试启动项仍存在')
    print('真实卸载通过，外部图库及无关文件完整；'+('占用项已报告' if locked else '程序文件夹已删除'), flush=True)


def main():
    import winreg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--case', choices=('all', 'smoke', 'normal', 'locked', 'running'), default='all')
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    expect(archive.with_name('SHA256SUMS.txt').read_text('ascii').strip() == f'{digest}  {archive.name}', 'SHA256 不符')
    base = Path(tempfile.gettempdir()).resolve()
    for case in (('smoke', 'normal', 'locked', 'running') if args.case == 'all' else (args.case,)):
        root = base/('whalemate-test-'+uuid4().hex)
        root.mkdir()
        try:
            env = environment(root)
            install = extract(archive, root)
            if case == 'smoke':
                smoke(install, root, env, '--smoke-test', 'bundle-smoke')
                smoke(install, root, env, '--pet-smoke-test', 'bundle-pet-smoke')
            else:
                uninstall_case(root, install, env, case)
        finally:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, test_key(root))
            except FileNotFoundError:
                pass
            if root.resolve() != root or root.parent != base:
                raise ValueError(f'拒绝清理异常测试路径：{root}')
            shutil.rmtree(root)


if __name__ == '__main__':
    main()
