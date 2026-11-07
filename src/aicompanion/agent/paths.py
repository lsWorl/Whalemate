"""路径辅助：受保护的位置（系统目录、凭据、浏览器配置、密钥文件），以及从任务原话里找出点名的文件夹。"""
from __future__ import annotations

import os
from pathlib import Path


def _protected_dirs() -> list[Path]:
    home = Path.home()
    env = lambda k, d='': Path(os.getenv(k, d)) if os.getenv(k, d) else None
    dirs = [env('SystemRoot', r'C:\Windows'), env('ProgramFiles'), env('ProgramFiles(x86)'), env('ProgramData'),
            home/'.ssh', home/'.aws', home/'.gnupg', home/'.azure', home/'.kube',
            home/'AppData'/'Roaming'/'Microsoft'/'Credentials', home/'AppData'/'Roaming'/'Microsoft'/'Protect',
            home/'AppData'/'Local'/'Microsoft'/'Credentials', home/'AppData'/'Local'/'Google'/'Chrome'/'User Data',
            home/'AppData'/'Local'/'Microsoft'/'Edge'/'User Data', home/'AppData'/'Roaming'/'Mozilla'/'Firefox']
    return [d for d in dirs if d]


PROTECTED_NAMES = {'.env', '.git-credentials', '.npmrc', '.pypirc', 'id_rsa', 'id_ed25519'}


def protected_reason(path: Path) -> str:
    """系统目录、凭据和浏览器配置、密钥文件、本应用的数据库：不论是否同意都不碰。"""
    if path.name.lower() in PROTECTED_NAMES or path.suffix.lower() in ('.pem', '.key', '.pfx', '.kdbx'):
        return '这是密钥 / 凭据文件'
    if path.suffix.lower() in ('.sqlite3', '.sqlite3-wal', '.sqlite3-shm') and 'aicompanion' in path.name.lower():
        return '这是我自己的数据库'
    if len(path.parts) <= 1: return '这是整个磁盘的根目录'
    for d in _protected_dirs():
        try:
            d = d.resolve()
            if path == d or d in path.parents: return f'这是系统或凭据目录（{d}）'
        except OSError:
            continue
    return ''


PATH_IN_TEXT = r'[A-Za-z]:\\[^\s"\'，。；、！？<>|*?]+'


def paths_in(text: str) -> list[Path]:
    """从一段文字里找出 Windows 绝对路径（任务说明、命令里点名的路径）。"""
    import re
    found = []
    for raw in re.findall(PATH_IN_TEXT, text or ''):
        raw = raw.rstrip('.,，。)）\'"`').replace('\\\\', '\\')
        found.append(Path(raw))
    return found


def task_folders(text: str) -> list[Path]:
    """用户在任务里点名的路径 → 所在文件夹（带扩展名的视为文件）。受保护的位置不算。"""
    folders = []
    from pathlib import PureWindowsPath
    for path in paths_in(text):
        win = PureWindowsPath(str(path))
        folder = Path(str(win.parent if win.suffix else win))
        try: resolved = folder.resolve()
        except OSError: continue
        if len(resolved.parts) > 1 and not protected_reason(resolved) and resolved not in folders: folders.append(resolved)
    return folders
