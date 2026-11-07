"""组件目录和安装记录。只动这个目录里的东西：%LOCALAPPDATA%\\AICompanion\\qq\\（可在高级设置里换）。

<根>/
  downloads/            已校验的下载（可复用，删了会重下）
  napcat/<版本>/         NapCat 自包含包（解压后原样，不修改）
  napcat-home/          NAPCAT_WORKDIR：config / logs / cache（二维码 cache/qrcode.png）
  qq-data/              NAPCAT_QQ_DATA_DIR：机器人 QQ 的数据，和你自己电脑上的 QQ 分开
  uv/<版本>/uv.exe       uv
  python/               uv 装的 Python 3.12
  astrbot/<版本>-<随机>/ AstrBot 的独立虚拟环境
  astrbot-home/         ASTRBOT_ROOT：AstrBot 的数据（插件、配置）
  logs/                 我们启动的进程的输出
  manifest.json         当前版本、来源、校验值、freeze、我们启动的进程
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile


def default_root() -> Path:
    return Path(os.getenv('LOCALAPPDATA') or Path.home()/'AppData'/'Local')/'AICompanion'/'qq'


@dataclass
class Layout:
    root: Path

    def __post_init__(self): self.root = Path(self.root)
    @property
    def downloads(self): return self.root/'downloads'
    @property
    def napcat_home(self): return self.root/'napcat-home'
    @property
    def qq_data(self): return self.root/'qq-data'
    @property
    def python_dir(self): return self.root/'python'
    @property
    def uv_cache(self): return self.root/'uv-cache'
    @property
    def astrbot_home(self): return self.root/'astrbot-home'
    @property
    def logs(self): return self.root/'logs'
    @property
    def manifest_path(self): return self.root/'manifest.json'

    # ---- 安装记录 -----------------------------------------------------------------
    def manifest(self) -> dict:
        try: return json.loads(self.manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError): return {}

    def save_manifest(self, data: dict) -> None:
        """先写临时文件再替换，断电也不会留下半个 manifest。"""
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.manifest-', dir=self.root)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle: json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(temp, self.manifest_path)

    def update_manifest(self, **changes) -> dict:
        data = self.manifest(); data.update(changes); self.save_manifest(data); return data

    def current(self, name: str) -> Path | None:
        """组件当前版本的目录（manifest 里登记、且目录还在才算）。"""
        entry = (self.manifest().get('components') or {}).get(name) or {}
        path = Path(entry['path']) if entry.get('path') else None
        return path if path and path.is_dir() else None

    def owns(self, path: Path) -> bool:
        try: Path(path).resolve().relative_to(self.root.resolve()); return True
        except (ValueError, OSError): return False


def safe_extract(zip_path: Path, target: Path) -> None:
    """解压并拒绝越界路径（../、绝对路径、盘符）。"""
    target = Path(target); target.mkdir(parents=True, exist_ok=True); base = target.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            name = member.filename.replace('\\', '/')
            dest = (target/name).resolve()
            if name.startswith('/') or ':' in name.split('/')[0] or (dest != base and base not in dest.parents):
                raise ValueError(f'压缩包里有越界路径：{member.filename}')
        archive.extractall(target)


def install_archive(layout: Layout, spec, zip_path: Path) -> Path:
    """解压到临时目录 → 检查关键文件 → 改名成 <name>/<版本>。同版本已装好就复用；失败时已有版本不动。"""
    final = layout.root/spec.name/spec.version
    if final.is_dir() and all((final/r).exists() for r in spec.required):
        return final
    staging = Path(tempfile.mkdtemp(prefix=f'.{spec.version}-', dir=_mkdir(layout.root/spec.name)))
    try:
        safe_extract(zip_path, staging)
        root = _single_top(staging, spec.required)
        missing = [r for r in spec.required if not (root/r).exists()]
        if missing: raise ValueError(f'{spec.name} 压缩包内容不对，缺少：{"、".join(missing)}')
        if final.exists():   # 同版本的残缺目录：挪到一边再换（只在我们自己的目录里）
            final.rename(layout.root/spec.name/f'.broken-{spec.version}-{int(time.time())}')
        root.rename(final)
        return final
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _single_top(staging: Path, required) -> Path:
    """有的包多包了一层同名目录：关键文件不在顶层、但在唯一的子目录里时，用那个子目录。"""
    if all((staging/r).exists() for r in required): return staging
    children = [p for p in staging.iterdir() if p.is_dir()]
    if len(children) == 1 and all((children[0]/r).exists() for r in required): return children[0]
    return staging


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True); return path
