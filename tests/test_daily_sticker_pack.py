"""素材准备只用临时目录与合成字节；不联网、不读取配置或用户图库。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from prepare_daily_stickers import prepare, public_url  # noqa: E402


class DailyStickerPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog/catalog.json"
        self.catalog.parent.mkdir()
        (self.catalog.parent / "licenses").mkdir()
        for name in ("README.md", "licenses/WHALE-ASSETS.txt", "licenses/TWEMOJI-CC-BY-4.0.txt"):
            (self.catalog.parent / name).write_text("合成许可夹具", encoding="utf-8")
        self.out = self.root / "result"
        self.raw = b"synthetic reviewed image bytes"
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.item = dict(file="images/a.png", sha256=self.sha,
                         metadata=dict(author="合成作者", license="CC-BY-4.0",
                                       license_evidence="synthetic:license", review_status="approved"),
                         download_url="https://kiriyuki.com/synthetic.png")
        self.env = patch.dict(os.environ, {"SystemDrive": "Z:"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write_catalog(self):
        self.catalog.write_text(json.dumps(dict(version=1, assets=[self.item])), encoding="utf-8")

    def test_bundled_file_and_repeat_preserve_file_mtime(self):
        self.item["bundled_file"] = "source.png"
        (self.catalog.parent / "source.png").write_bytes(self.raw)
        self.write_catalog()
        network = Mock(side_effect=AssertionError("不应联网"))
        self.assertEqual(prepare(self.catalog, self.out, downloader=network)["bundled"], 1)
        stamp = (self.out / self.item["file"]).stat().st_mtime_ns
        self.assertEqual(prepare(self.catalog, self.out, downloader=network)["existing"], 1)
        self.assertEqual((self.out / self.item["file"]).stat().st_mtime_ns, stamp)
        network.assert_not_called()

    def test_remote_hash_is_verified(self):
        self.write_catalog()
        network = Mock(return_value=self.raw)
        stats = prepare(self.catalog, self.out, downloader=network)
        self.assertEqual(stats["downloaded"], 1)
        self.assertEqual((self.out / self.item["file"]).read_bytes(), self.raw)
        network.assert_called_once()

    def test_hash_failure_does_not_publish_manifest(self):
        self.write_catalog()
        with self.assertRaises(ValueError):
            prepare(self.catalog, self.out, downloader=lambda _: b"wrong image")
        self.assertFalse((self.out / self.item["file"]).exists())
        self.assertFalse((self.out / "manifest.json").exists())

    def test_conflicting_target_is_preserved(self):
        self.write_catalog()
        target = self.out / self.item["file"]
        target.parent.mkdir(parents=True)
        target.write_bytes(b"keep custom file")
        with self.assertRaises(FileExistsError):
            prepare(self.catalog, self.out, downloader=Mock())
        self.assertEqual(target.read_bytes(), b"keep custom file")

    def test_paths_and_download_hosts_are_checked_before_requests(self):
        for change in (dict(file="../outside.png"), dict(download_url="http://127.0.0.1/private"),
                       dict(bundled_file="../outside.png")):
            original = dict(self.item)
            self.item.update(change)
            self.write_catalog()
            network = Mock()
            with self.assertRaises(ValueError):
                prepare(self.catalog, self.out, downloader=network)
            network.assert_not_called()
            self.item = original

    def test_existing_reviewed_pack_is_reused_without_network(self):
        path = self.root / "old"
        path.mkdir()
        (path / "old.png").write_bytes(self.raw)
        old = path / "manifest.json"
        old.write_text(json.dumps(dict(assets=[dict(file="old.png", sha256=self.sha)])))
        self.write_catalog()
        stats = prepare(self.catalog, self.out, reuse=[old], downloader=Mock(side_effect=AssertionError()))
        self.assertEqual(stats["reused"], 1)

    def test_unknown_license_and_oversized_bytes_are_rejected(self):
        self.item["metadata"]["license"] = "unknown"
        self.write_catalog()
        network = Mock()
        with self.assertRaises(ValueError):
            prepare(self.catalog, self.out, downloader=network)
        network.assert_not_called()
        self.item["metadata"]["license"] = "CC-BY-4.0"
        self.write_catalog()
        with self.assertRaises(ValueError):
            prepare(self.catalog, self.out, downloader=lambda _: b"x" * (1024*1024+1))

    def test_other_hosts_or_credentials_in_redirect_are_rejected(self):
        for url in ("https://kiriyuki.com.evil.test/a.png", "https://user:pass@kiriyuki.com/a.png",
                    "https://raw.githubusercontent.com:444/a.png"):
            with self.assertRaises(ValueError):
                public_url(url)

    def test_missing_license_cannot_publish_or_download(self):
        self.write_catalog()
        (self.catalog.parent / "licenses/TWEMOJI-CC-BY-4.0.txt").unlink()
        network = Mock()
        with self.assertRaises(FileNotFoundError):
            prepare(self.catalog, self.out, downloader=network)
        network.assert_not_called()
        self.assertFalse((self.out / "manifest.json").exists())
