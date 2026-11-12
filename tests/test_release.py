"""发布边界与卸载预览回归。所有写入与模拟的企业/用户路径均在临时目录。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from aicompanion import uninstall as u
from aicompanion.release_manifest import inventory
from tools.build_release import PUBLIC, check_member, package

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_prohibited_files_fail_even_inside_internal(self):
        for member in ('.env', 'docs/.env.example', 'runtime/.env.bak', 'Data/key',
                       'profiles/save.sqlite3-wal', 'chat.db', 'one.LOG', 'a/__pycache__/x',
                       '_internal/a/animation-aboutme/atlas.png', 'output/aboutme-cache/a.webp',
                       'vendor/Live2DCubismCore.min.js', 'bin/Live2DCubismCore.dll'):
            with self.subTest(member=member), self.assertRaises(ValueError):
                check_member(member)
        for member in ('.env.example', 'docs/aboutme-pet.md', 'tools/install_aboutme_pet.py',
                       '_internal/aicompanion/characters/deepseek-whale/rig/idle.png'):
            check_member(member)

    def test_release_rejects_non_english_file_and_directory_names(self):
        for path in ('卸载鲸伴.exe', '_internal/角色/idle.png'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, '英文命名'):
                check_member(path)
        check_member('UninstallWhalemate.exe')

    def test_packaging_rejects_contaminated_staging_before_writing_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            bundle = work/'stage'
            for name in (*PUBLIC, 'AICompanion.exe', 'UninstallWhalemate.exe', '_internal/fixture.bin'):
                file = bundle/name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            (bundle/u.MANIFEST).write_bytes(inventory(bundle))
            for name, content in (('nested/.env', b'fixture'), ('_internal/fixture.bin', b'SQLite format 3\0')):
                file = bundle/name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
                with self.assertRaisesRegex(ValueError, '禁止分发|SQLite 数据库'):
                    package(work, bundle, work/'release', '0.1.0')
                self.assertFalse((work/'release').exists())
                file.unlink()

    def test_archive_contains_exact_manifest_and_detects_changes_after_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root/'stage'
            for name in (*PUBLIC, 'AICompanion.exe', u.EXE, '_internal/assets/test.png'):
                path = bundle/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'test')
            (bundle/'_internal/empty').mkdir()
            manifest = bundle/u.MANIFEST
            manifest.write_bytes(inventory(bundle))
            package(root, bundle, root/'release', '1.2.3')
            with zipfile.ZipFile(root/'release/Whalemate-v1.2.3-windows-x64.zip') as archive:
                data = json.loads(archive.read('Whalemate/'+u.MANIFEST))
                self.assertEqual(set(data['files']), {n.removeprefix('Whalemate/') for n in archive.namelist() if not n.endswith('/')})
                self.assertIn('_internal/empty', data['directories'])
                self.assertEqual(data['files']['AICompanion.exe']['size'], 4)
            (bundle/'_internal/extra.txt').touch()
            with self.assertRaisesRegex(ValueError, '清单不一致'):
                package(root, bundle, root/'release', '1.2.3')

    def test_document_lists_all_categories(self):
        table = (ROOT/'docs/storage-locations.md').read_text('utf-8').split('## 卸载会删除哪些内容')[1]
        rows = [line.split('|')[1].strip() for line in table.splitlines() if line.startswith('|')][2:]
        self.assertCountEqual(rows, u.CATEGORIES)


@unittest.skipUnless(os.name == 'nt', 'Windows 卸载器')
class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.install = self.root/'Whalemate'
        self.file('Whalemate/_internal/rig/texture.png')
        self.file('Whalemate/AICompanion.exe')
        self.file('Whalemate/'+u.EXE)
        self.publish_manifest()
        self.record = self.root/'paths.json'
        self.memory = self.root/'work/memory'
        self.gallery = self.root/'我的图库'
        self.memory.mkdir(parents=True)
        self.db = self.memory/'aicompanion.sqlite3'
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE app_settings(key,value)')
        conn.close()
        self.record.write_text(json.dumps(dict(memory_root=str(self.memory), meme_root=str(self.gallery))), 'utf-8')
        self.env = self.enterContext(patch.dict(os.environ, {'AICOMPANION_PATHS_FILE': str(self.record), 'DATABASE_PATH': ''}))
        self.loc = u.Locations(self.install, self.root/'disk/AICompanion/data', self.root/'home/Local/AICompanion',
                               self.root/'home', (self.root/'disk',), fence=self.root)
        self.enterContext(patch.object(u, 'registry_value', return_value=None))
        self.real_stop = u.ensure_stopped
        self.stopped = self.enterContext(patch.object(u, 'ensure_stopped'))

    def publish_manifest(self):
        self.trusted = inventory(self.install)
        (self.install/u.MANIFEST).write_bytes(self.trusted)

    def scan(self):
        return u.scan(self.loc, self.trusted)

    def file(self, relative):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
        return path

    def test_all_documented_categories_appear_in_a_used_installation(self):
        for name in ('我的图库/cat.png', 'disk/AICompanion/data/memes/默认.png', 'home/Local/AICompanion/qq/uv/fixture',
                     'home/Local/AICompanion/voice-cache/a.wav', 'home/AppData/LocalLow/AICompanion/dsh/session',
                     'disk/.aicompanion-trash/fixture', 'Whalemate/user-note.txt'):
            self.file(name)
        with patch.object(u, 'registry_value', return_value=(f'"{self.install/"AICompanion.exe"}"', 1)):
            lines = list(self.scan().lines())
        self.assertEqual({line.split('\t')[2] for line in lines}, set(u.CATEGORIES))

    def test_preview_confirmation_preserves_other_files(self):
        other = self.file('work/memory/unrelated.sqlite3')
        mine = self.file('我的图库/cat.png')
        backup = self.file('work/memory/aicompanion.backup-old.sqlite3')
        default = self.file('disk/AICompanion/data/memes/cat.png')
        plan = self.scan()
        self.assertIn(str(mine), '\n'.join(plan.lines()))
        with self.assertRaises(ValueError):
            u.execute(plan, confirmed=False)
        self.assertTrue(backup.exists())
        _, failed = u.execute(plan, confirmed=True)
        self.assertFalse(failed)
        self.assertTrue(mine.exists() and other.exists())
        self.assertFalse(any(path.exists() for path in (backup, default, self.db, self.record, self.install)))

    def test_opt_in_and_gallery_nested_in_local(self):
        nested = self.file('home/Local/AICompanion/pictures/cat.png')
        self.record.write_text(json.dumps({'meme_root': str(nested.parent)}), 'utf-8')
        plan = self.scan()
        self.assertNotIn(nested, [i.path for i in plan.selected()])
        self.assertNotIn(self.loc.local, [i.path for i in plan.selected()])
        self.assertFalse(u.execute(plan, confirmed=True, galleries=True)[1])
        self.assertFalse(nested.exists())

    def test_preview_reads_wal_settings_without_modifying_database_or_sidecars(self):
        gallery = self.file('增量图库/mine.png').parent
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('INSERT INTO app_settings VALUES (?,?)', ('qq_meme_root', json.dumps(str(gallery))))
        conn.commit()
        before = {p: p.read_bytes() for p in self.memory.iterdir()}
        plan = self.scan()
        self.assertIn(gallery, plan.galleries)
        self.assertEqual(before, {p: p.read_bytes() for p in self.memory.iterdir()})

    def test_record_cannot_select_an_unrelated_custom_database(self):
        private = self.file('work/memory/accounts.db')
        backup = self.file('work/memory/accounts.backup-old.sqlite3')
        self.record.write_text(json.dumps({'memory_root': str(self.memory), 'database_name': private.name}), 'utf-8')
        plan = self.scan()
        self.assertTrue(plan.notes)
        self.assertFalse(u.execute(plan, confirmed=True)[1])
        self.assertTrue(private.exists() and backup.exists())

    def test_database_only_qq_and_gallery_settings(self):
        mine = self.file('我的图库/cat.png')
        qq = self.root/'自选组件'
        manifest = self.file('自选组件/manifest.json')
        manifest.write_text('{"components":{}}', 'utf-8')
        managed = self.file('自选组件/astrbot-home/plugin.txt')
        private = self.file('自选组件/我的文件.txt')
        self.record.write_text(json.dumps({'memory_root': str(self.memory)}), 'utf-8')
        with sqlite3.connect(self.db) as conn:
            conn.executemany('INSERT INTO app_settings VALUES (?,?)',
                             [('qq_root', json.dumps(str(qq))), ('qq_meme_root', json.dumps(str(mine.parent)))])
        conn.close()
        plan = self.scan()
        self.assertIn(qq, plan.qq)
        self.assertFalse(u.execute(plan, confirmed=True)[1])
        self.assertTrue(private.exists() and mine.exists())
        self.assertFalse(managed.exists())

    def test_root_home_system_and_missing_program_markers_refused_as_a_whole(self):
        for path in (Path(self.root.anchor), self.loc.home, Path(os.environ['SystemRoot']),
                     self.loc.home.parent, Path(os.environ['SystemRoot'])/'System32'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                replace(self.loc, fence=None).check(path)
        for marker in ('AICompanion.exe', '_internal', u.EXE):
            path = self.install/marker
            moved = path.with_name('saved')
            path.rename(moved)
            with self.assertRaises(ValueError):
                self.scan()
            moved.rename(path)
            self.assertTrue(self.db.exists())

    def test_late_files_and_locked_file_reported_individually(self):
        locked = self.file('Whalemate/locked.txt')
        sibling = self.file('Whalemate/other.txt')
        self.publish_manifest()
        plan = self.scan()
        new = self.file('Whalemate/new.txt')
        with locked.open('rb'):
            ok, failed = u.execute(plan, confirmed=True)
        self.assertIn(str(sibling), ok)
        self.assertTrue(any(str(locked) in e for e in failed))
        self.assertTrue(new.exists() and locked.exists())
        self.assertFalse(self.db.exists())

    def test_desktop_preserves_user_files_and_shipped_parent_directories(self):
        desktop = self.loc.home/'Desktop'
        desktop.parent.mkdir(exist_ok=True)
        self.install.rename(desktop)
        self.install = desktop
        self.loc = replace(self.loc, install=desktop)
        mine = [self.file('home/Desktop/notes.txt'), self.file('home/Desktop/School/project.txt'),
                self.file('home/Desktop/_internal/rig/my-drawing.png')]
        lines = list(self.scan().lines())
        for path in (desktop, mine[0], mine[1].parent, mine[2]):
            self.assertTrue(any(s.startswith('保留\t') and s.endswith('\t'+str(path)) for s in lines))
        self.assertFalse(u.execute(self.scan(), confirmed=True)[1])
        self.assertTrue(all(p.read_bytes() == b'fixture' for p in mine))
        self.assertTrue(desktop.is_dir())
        self.assertFalse((desktop/u.EXE).exists())
        self.assertFalse((desktop/'_internal/rig/texture.png').exists())

    def test_shipped_file_edited_after_preview_requires_rescan(self):
        path = self.install/'_internal/rig/texture.png'
        plan = self.scan()
        timestamp = path.stat().st_mtime_ns
        path.write_bytes(b'private')
        os.utime(path, ns=(timestamp, timestamp+2_000_000_000))
        with self.assertRaisesRegex(ValueError, '重新预览'):
            u.execute(plan, confirmed=True)
        self.assertTrue(self.db.exists())
        self.assertFalse(u.execute(self.scan(), confirmed=True)[1])
        self.assertEqual(path.read_bytes(), b'private')

    def test_empty_personal_directories_are_kept(self):
        for name in ('Desktop', 'Downloads', 'Documents', 'OneDrive'):
            path = self.loc.home/name
            shutil.copytree(self.install, path)
            plan = u.scan(replace(self.loc, install=path), self.trusted)
            self.assertFalse(u.execute(plan, confirmed=True)[1])
            self.assertEqual(list(path.iterdir()), [])

    def test_unrelated_files_with_runtime_like_names_are_retained(self):
        keep = [self.file('Whalemate/vacation.webp'), self.file('Whalemate/output/notes.txt'),
                self.file('Whalemate/animation-trip/poster.png'), self.file('Whalemate/meeting.log')]
        # 同路径、同大小，内容不同的私人文件也不能认领。
        replaced = self.install/'_internal/rig/texture.png'
        replaced.write_bytes(b'private')
        keep.append(replaced)
        plan = self.scan()
        self.assertFalse(u.execute(plan, confirmed=True)[1])
        self.assertTrue(all(p.is_file() for p in keep))

    def test_local_appdata_install_still_requires_file_ownership(self):
        self.loc.local.parent.mkdir(parents=True)
        self.install.rename(self.loc.local)
        self.install = self.loc.local
        self.loc = replace(self.loc, install=self.install)
        private = self.file('home/Local/AICompanion/personal.txt')
        self.assertFalse(u.execute(self.scan(), confirmed=True)[1])
        self.assertTrue(private.exists())

    def test_mismatched_manifest_aborts_before_deletion(self):
        manifest = self.install/u.MANIFEST
        data = json.loads(self.trusted)
        mine = self.file('Whalemate/personal.txt')
        for name in ('personal.txt', '../work/memory/aicompanion.sqlite3', 'C:/Windows/not-ours'):
            data['files'][name] = {'size': 7, 'sha256': '0'*64}
            manifest.write_text(json.dumps(data), 'utf-8')
            with self.assertRaises(ValueError):
                self.scan()
        manifest.unlink()
        with self.assertRaises(ValueError):
            self.scan()
        self.assertTrue(self.db.exists() and mine.exists())

    def test_trash_and_gallery_are_independent_and_cover_all_locations(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute('INSERT INTO app_settings VALUES (?,?)', ('blog_root', json.dumps(str(self.root/'blog'))))
        conn.close()
        gallery = self.file('我的图库/cat.png')
        files = [self.file(p) for p in ('disk/.aicompanion-trash/doc', 'disk/.aicompanion-snapshots/doc',
                                        'home/Local/AICompanion/trash/doc', 'blog/.aicompanion-trash/doc')]
        plan = self.scan()
        for galleries, trash in ((False, False), (False, True), (True, False), (True, True)):
            paths = {item.path for item in plan.selected(galleries, trash)}
            self.assertEqual(gallery in paths, galleries)
            self.assertTrue(all((path in paths) == trash for path in files))
            self.assertEqual(self.loc.local in paths, trash)
        self.assertFalse(u.execute(plan, confirmed=True, trash=True)[1])
        self.assertTrue(gallery.is_file())
        self.assertTrue(all(not p.exists() for p in files))

    def test_other_installation_startup_entry_is_kept(self):
        with patch.object(u, 'registry_value', return_value=(str(self.root/'other/AICompanion.exe'), 1)):
            self.assertIsNone(self.scan().startup)

    def test_running_refuses_before_deleting(self):
        self.stopped.side_effect = ValueError('请先退出')
        with self.assertRaisesRegex(ValueError, '退出'):
            u.execute(self.scan(), confirmed=True)
        self.assertTrue(self.db.exists() and self.install.exists())

    def test_nested_external_memory_keeps_unrelated_files(self):
        memory = self.install/'旧存储'
        mine = self.file('Whalemate/旧存储/本人文件.txt')
        self.db.rename(memory/self.db.name)
        self.record.write_text(json.dumps({'memory_root': str(memory)}), 'utf-8')
        plan = self.scan()
        self.assertFalse(u.execute(plan, confirmed=True)[1])
        self.assertTrue(mine.exists())
        self.assertFalse((memory/self.db.name).exists())

    def test_stops_only_recorded_qq_process_and_removes_original_manifest(self):
        qq = self.loc.local/'qq'
        qq.mkdir(parents=True)
        binary = qq/'test-ping.exe'
        shutil.copyfile(Path(os.environ['SystemRoot'])/'System32/ping.exe', binary)
        supervisor = u.Supervisor(u.Layout(qq))
        supervisor.start('napcat', [binary, '-t', '127.0.0.1'], qq, dict(os.environ))
        child = supervisor.children['napcat']
        try:
            plan = self.scan()
            # 此用例真实运行检查和 stop；仅终止临时目录里有匹配启动记录的进程。
            self.stopped.side_effect = self.real_stop
            self.assertFalse(u.execute(plan, confirmed=True)[1])
            self.assertIsNotNone(child.poll())
            self.assertFalse(qq.exists())
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)

    def test_junction_during_preview_and_after_confirmation(self):
        target = self.file('unrelated/mine.txt')
        folder = self.install/'child'
        folder.mkdir()
        plan = self.scan()
        folder.rmdir()
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(folder), str(target.parent)],
                       check=True, capture_output=True)
        try:
            with self.assertRaises(ValueError):
                self.scan()
            with self.assertRaises(ValueError):
                u.execute(plan, confirmed=True)
            self.assertTrue(self.db.exists() and target.exists())
        finally:
            folder.rmdir()


if __name__ == '__main__':
    unittest.main()
