"""下载并校验：先写到 .part，SHA-256 对上才改名；已经校验过的文件直接复用；失败不留下半截文件。"""
from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request


class FetchError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''): digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, dest: Path, sha256: str, progress=None, cancelled=lambda: False, opener=urllib.request.urlopen,
          timeout: float = 60) -> Path:
    """下载 url 到 dest 并校验。progress(已下载字节, 总字节或 0)；cancelled() 为真时中止。"""
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and sha256_of(dest) == sha256.lower():
        return dest
    part = dest.with_name(dest.name+'.part')
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'AICompanion-QQ-Setup'})
        with opener(request, timeout=timeout) as response, open(part, 'wb') as out:
            total = int(response.headers.get('Content-Length') or 0); done = 0
            while True:
                if cancelled(): raise FetchError('已取消')
                chunk = response.read(1 << 20)
                if not chunk: break
                out.write(chunk); done += len(chunk)
                if progress: progress(done, total)
        actual = sha256_of(part)
        if actual != sha256.lower():
            raise FetchError(f'下载的文件校验不一致（期望 {sha256[:12]}…，实际 {actual[:12]}…），可能被篡改或下载不完整，已丢弃')
        part.replace(dest)
        return dest
    except FetchError:
        raise
    except Exception as error:
        raise FetchError(f'下载失败：{error}') from error
    finally:
        if part.exists():
            try: part.unlink()
            except OSError: pass
