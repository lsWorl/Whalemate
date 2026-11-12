"""准备可导入的日常贴纸包：只取审核目录里的固定字节，不读应用配置或聊天库。

输出是开发素材包，不是新的图库根目录配置；用户仍从现有图库页面导入 manifest.json。
重复执行校验 SHA 并复用文件，不覆盖冲突文件。下载只允许已经核对许可的公开来源。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "assets/stickers/daily-reactions-v2/catalog.json"
MAX_BYTES = 1024 * 1024
HOSTS = frozenset(("kiriyuki.com", "raw.githubusercontent.com"))
LICENSES = frozenset(("kiriyuki-noncommercial-attribution", "CC-BY-4.0"))
SUFFIXES = frozenset((".png", ".gif", ".jpg", ".jpeg", ".webp"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def child(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() not in SUFFIXES:
        raise ValueError("素材路径越界或格式不支持")
    return path


def public_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("下载地址不属于已审核的公开 HTTPS 来源")
    return url


class SourceRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    request = Request(public_url(url), headers={"User-Agent": "AICompanion-sticker-materials/1.0"})
    with build_opener(SourceRedirects()).open(request, timeout=30) as response:
        return response.read(MAX_BYTES + 1)


def write_once(path, data):
    """校验后原子落地；临时文件只清理本次自己创建的，目标冲突原样保留。"""
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError("目标文件已存在且内容不同，未覆盖")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sticker-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            temporary.rename(path)  # Windows rename 不覆盖已存在文件。
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def reuse_files(manifests):
    cache = {}
    for manifest in manifests:
        manifest = Path(manifest).resolve()
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        for item in data.get("assets", []):
            source = child(manifest.parent, item["file"])
            if source.is_file() and source.stat().st_size <= MAX_BYTES:
                raw = source.read_bytes()
                if digest(raw) == item["sha256"]:
                    cache.setdefault(item["sha256"], raw)
    return cache


def prepare(catalog, output, reuse=(), downloader=fetch):
    catalog, output = Path(catalog).resolve(), Path(output).resolve()
    if os.name == "nt" and output.drive.casefold() == os.getenv("SystemDrive", "C:").casefold():
        raise ValueError("请将素材包输出到非系统盘")
    data = json.loads(catalog.read_text(encoding="utf-8-sig"))
    if data.get("version") != 1 or not isinstance(data.get("assets"), list):
        raise ValueError("素材目录版本不支持")
    # 先校验全部目录，避免遇到非法条目时前面已进行网络请求。
    for item in data["assets"]:
        child(output, item["file"])
        sha = item.get("sha256", "")
        meta = item.get("metadata", {})
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("素材 SHA256 无效")
        if (meta.get("review_status") != "approved" or meta.get("license") not in LICENSES
                or not meta.get("author") or not meta.get("license_evidence")):
            raise ValueError("未知许可或待审核素材不能进入正式包")
        if item.get("bundled_file"):
            child(catalog.parent, item["bundled_file"])
        else:
            public_url(item.get("download_url", ""))
    extras = {}
    for name in ("README.md", "licenses/WHALE-ASSETS.txt", "licenses/TWEMOJI-CC-BY-4.0.txt"):
        source = (catalog.parent / name).resolve()
        target = (output / name).resolve()
        if not source.is_relative_to(catalog.parent) or not target.is_relative_to(output):
            raise ValueError("署名或许可路径越界")
        raw = source.read_bytes()
        if target.exists() and target.read_bytes() != raw:
            raise FileExistsError("目标署名或许可文件冲突，未覆盖")
        extras[target] = raw
    cache = reuse_files(reuse)
    stats = dict(total=len(data["assets"]), existing=0, reused=0, bundled=0, downloaded=0, bytes=0)
    result = []
    for item in data["assets"]:
        target = child(output, item["file"])
        expected = item["sha256"]
        if target.exists():
            if target.stat().st_size > MAX_BYTES or digest(target.read_bytes()) != expected:
                raise FileExistsError("目标素材冲突，未覆盖")
            stats["existing"] += 1
            stats["bytes"] += target.stat().st_size
        else:
            if expected in cache:
                raw = cache[expected]
                source = "reused"
            elif item.get("bundled_file"):
                raw = child(catalog.parent, item["bundled_file"]).read_bytes()
                source = "bundled"
            else:
                raw = downloader(item["download_url"])
                source = "downloaded"
            if not raw or len(raw) > MAX_BYTES or digest(raw) != expected:
                raise ValueError("素材体积或 SHA256 校验失败，未写入")
            write_once(target, raw)
            stats[source] += 1
            stats["bytes"] += len(raw)
        result.append({key: item[key] for key in ("file", "sha256", "metadata", "asset_id", "revision") if key in item})
    manifest = dict(version=1, name=data.get("name", "日常贴纸包"), assets=result)
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # 署名与许可先完成，最后才发布可导入清单，避免不完整包被误用。
    for target, raw in extras.items():
        write_once(target, raw)
    manifest_path = (output / "manifest.json").resolve()
    if not manifest_path.is_relative_to(output):
        raise ValueError("清单路径越界")
    write_once(manifest_path, payload)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reuse-manifest", type=Path, action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(prepare(args.catalog, args.out, args.reuse_manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
