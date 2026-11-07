"""回收区和 DSH 旧会话的自动清理。"""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from aicompanion.agent import housekeeping


class HousekeepingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.root/'local')}); self.env.start()
        self.trash = self.root/'local'/'AICompanion'/'trash'; self.trash.mkdir(parents=True)
        self.now = time.time()

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def batch(self, folder, name, days, size=10):
        path = folder/name; path.mkdir(parents=True); (path/'f.txt').write_bytes(b'x'*size)
        stamp = self.now-days*86400; os.utime(path, (stamp, stamp)); return path

    def test_old_batches_removed(self):
        old = self.batch(self.trash, '20261001-run1', 30); new = self.batch(self.trash, '20261106-run2', 1)
        report = housekeeping.clean(14, 10**9, now=self.now, drives=False)
        self.assertFalse(old.exists()); self.assertTrue(new.exists())
        self.assertEqual((report.removed, report.kept), (1, 1)); self.assertIn('释放', report.text())

    def test_size_cap_removes_oldest_first(self):
        a = self.batch(self.trash, 'a', 3, 600); b = self.batch(self.trash, 'b', 2, 600); c = self.batch(self.trash, 'c', 1, 600)
        housekeeping.clean(14, 1300, now=self.now, drives=False)
        self.assertFalse(a.exists()); self.assertTrue(b.exists() and c.exists())

    def test_zero_days_keeps_by_age(self):
        old = self.batch(self.trash, 'old', 400)
        housekeeping.clean(0, 10**9, now=self.now, drives=False); self.assertTrue(old.exists())

    def test_legacy_blog_trash_and_sessions(self):
        blog = self.root/'blog'; legacy = blog/'.aicompanion-trash'; legacy.mkdir(parents=True)
        old_file = legacy/'20261001-120000_post.md'; old_file.write_text('x'); stamp = self.now-40*86400; os.utime(old_file, (stamp, stamp))
        home = self.root/'dsh'; session = home/'sessions'/'--D-ws--'/'s-old'; session.mkdir(parents=True)
        cache = home/'storages'/'session_projcache'/'sessions'; cache.mkdir(parents=True); (cache/'s-old.json').write_text('{}')
        os.utime(session, (stamp, stamp))
        fresh = home/'sessions'/'--D-ws--'/'s-new'; fresh.mkdir()
        report = housekeeping.clean(14, 10**9, [blog], home, now=self.now, drives=False)
        self.assertFalse(old_file.exists()); self.assertFalse(session.exists()); self.assertFalse((cache/'s-old.json').exists())
        self.assertTrue(fresh.exists()); self.assertEqual(report.sessions, 1)


if __name__ == '__main__':
    unittest.main()
