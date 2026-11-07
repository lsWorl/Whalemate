"""回收区和 DSH 旧会话的自动清理。

回收区（她改文件前的旧版本、删掉的文件）在：
- %LOCALAPPDATA%\\AICompanion\\trash（C 盘上的文件）
- <盘>:\\.aicompanion-trash（其他盘上的文件）
- 以前自研 agent 留下的 <博客目录>\\.aicompanion-trash（旧格式：时间戳_文件名）

规则：超过保留天数的整批删掉；总量超过上限时从最旧的开始删。DSH 的会话记录（每个任务一个）同样按天数清理。
另外一次性删掉早期隔离实验留下的 <盘>:\\.aicompanion-snapshots。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import string
import time

from aicompanion.agent.tools_server import TRASH_NAME

DEFAULT_KEEP_DAYS = 14
DEFAULT_MAX_BYTES = 2*1024**3


@dataclass
class Report:
    removed: int = 0          # 删掉的批次 / 文件数
    freed: int = 0            # 释放的字节
    kept: int = 0             # 留下的批次数
    sessions: int = 0         # 清掉的 DSH 会话

    def text(self) -> str:
        return (f'清掉 {self.removed} 批旧文件，释放 {self.freed/1024/1024:.1f} MB；回收区还留 {self.kept} 批'
                + (f'；清掉 {self.sessions} 个旧会话记录' if self.sessions else '') + '。')


def _drives() -> list[Path]:
    if os.name != 'nt': return []
    return [Path(f'{letter}:\\') for letter in string.ascii_uppercase if os.path.exists(f'{letter}:\\')]


def trash_roots(extra: list[str | Path] = (), drives: bool = True) -> list[Path]:
    """所有存在的回收区目录。drives=False 时不扫描各盘根目录（测试用）。"""
    roots = [Path(os.getenv('LOCALAPPDATA') or Path.home())/'AICompanion'/'trash']
    roots += [drive/TRASH_NAME for drive in (_drives() if drives else [])]
    roots += [Path(p)/TRASH_NAME for p in extra if p]
    seen, out = set(), []
    for root in roots:
        key = str(root).lower()
        if key not in seen and root.is_dir(): seen.add(key); out.append(root)
    return out


def _size(path: Path) -> int:
    if path.is_file(): return path.stat().st_size
    total = 0
    for base, _, files in os.walk(path):
        for name in files:
            try: total += os.path.getsize(os.path.join(base, name))
            except OSError: pass
    return total


def _remove(path: Path) -> bool:
    try:
        if path.is_dir(): shutil.rmtree(path)
        else: path.unlink()
        return True
    except OSError:
        return False


def clean(keep_days: float = DEFAULT_KEEP_DAYS, max_bytes: int = DEFAULT_MAX_BYTES, extra_roots: list = (),
          dsh_home: Path | None = None, now: float | None = None, drives: bool = True) -> Report:
    """清理回收区和 DSH 旧会话。keep_days <= 0 表示不按天数清（总量上限仍然生效）。"""
    now = now or time.time(); report = Report()
    batches = []   # (修改时间, 大小, 路径)
    for root in trash_roots(extra_roots, drives):
        for item in root.iterdir():
            try: batches.append([item.stat().st_mtime, _size(item), item])
            except OSError: continue
    batches.sort(key=lambda b: b[0])
    total = sum(b[1] for b in batches)
    for batch in batches:
        too_old = keep_days > 0 and now-batch[0] > keep_days*86400
        if too_old or total > max_bytes:
            if _remove(batch[2]):
                report.removed += 1; report.freed += batch[1]; total -= batch[1]
                continue
        report.kept += 1
    for drive in (_drives() if drives else []):   # 早期隔离实验的硬链接快照，现在用不上了
        legacy = drive/'.aicompanion-snapshots'
        if legacy.is_dir():
            size = _size(legacy)
            if _remove(legacy): report.freed += size
    if dsh_home is not None and keep_days > 0:
        report.sessions = _clean_sessions(dsh_home, keep_days, now)
    return report


def _clean_sessions(home: Path, keep_days: float, now: float) -> int:
    """DSH 每个任务一个会话：sessions/<工作区>/<会话号>/ 和 storages/session_projcache/sessions/<会话号>.json。"""
    count = 0
    sessions = home/'sessions'
    if sessions.is_dir():
        for workspace in sessions.iterdir():
            if not workspace.is_dir(): continue
            for session in workspace.iterdir():
                try: old = now-session.stat().st_mtime > keep_days*86400
                except OSError: continue
                if old and _remove(session):
                    count += 1
                    cache = home/'storages'/'session_projcache'/'sessions'/f'{session.name}.json'
                    if cache.exists(): _remove(cache)
            try:
                if not any(workspace.iterdir()): workspace.rmdir()
            except OSError:
                pass
    return count
