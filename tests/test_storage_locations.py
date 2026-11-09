"""磁盘选择和记忆迁移只用临时库；不读取 .env、真实数据库或用户图库。"""
from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from aicompanion.config import Settings, load_settings
from aicompanion.storage.database import Database
from aicompanion.storage import locations


class StorageBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.environment = mock.patch.dict(os.environ, {'SystemDrive': 'Z:'})  # 临时目录所在 C 盘不是本测试的系统盘
        self.environment.start()
        self.source = self.root/'old'/'aicompanion.sqlite3'; self.db = Database(self.source); self.db.initialize()
        self.db.add_message('user', '合成聊天 4827'); self.db.upsert_memory('合成记忆蓝色', fact_key='test:blue')
        self.db.set_setting('synthetic_setting', 'preserved')
        self.record = self.root/'paths.json'; self.target_root = self.root/'new'; self.memes = self.root/'memes'
        self.settings = Settings('test', self.source, 'mock', '', '', 'mock', .8, 10,
                                 storage_paths_file=self.record, meme_directory=self.memes)
    def tearDown(self):
        self.environment.stop(); self.tmp.cleanup()

    def select(self):
        return locations.plan_change(self.source, self.target_root, self.memes, self.record)


class LocationMigrationTests(StorageBase):
    def test_copy_preserves_messages_memory_settings_and_old_database(self):
        plan = self.select()
        target = self.target_root/self.source.name
        self.assertFalse(target.exists())  # 保存设置时绝不复制仍有后台写入的库
        self.db.add_message('user', '保存设置后的最后一句')
        new_settings = replace(self.settings, database_path=target, migration_source=self.source)
        locations.prepare(new_settings)
        new = Database(target)
        self.assertEqual([r['content'] for r in new.recent_messages()], ['合成聊天 4827', '保存设置后的最后一句'])
        self.assertEqual(new.recent_memories()[0]['content'], '合成记忆蓝色')
        self.assertEqual(new.get_setting('synthetic_setting'), 'preserved')
        self.assertTrue(self.source.is_file()); self.assertNotIn('migration', locations.read_record(self.record))
        self.assertTrue((self.memes/'开心庆祝'/'celebrate.png').is_file())
        locations.prepare(new_settings)  # 重入不会再复制旧数据
        self.assertEqual(len(new.recent_messages()), 2)

    def test_existing_destination_is_never_overwritten(self):
        self.target_root.mkdir(); target = self.target_root/self.source.name; target.write_bytes(b'keep unrelated database')
        with self.assertRaises(ValueError): self.select()
        self.assertEqual(target.read_bytes(), b'keep unrelated database'); self.assertFalse(self.record.exists())

    def test_destination_appearing_after_plan_is_not_overwritten(self):
        plan = self.select(); target = self.target_root/self.source.name; target.write_bytes(b'created later')
        with self.assertRaises(ValueError): locations.prepare(replace(self.settings, database_path=target, migration_source=self.source))
        self.assertEqual(target.read_bytes(), b'created later'); self.assertTrue(self.source.exists())

    def test_corrupt_source_leaves_old_and_target_unpublished(self):
        plan = self.select(); before = self.source.read_bytes(); self.source.write_bytes(b'not a database')
        target = self.target_root/self.source.name
        with self.assertRaises(sqlite3.DatabaseError): locations.prepare(replace(self.settings, database_path=target, migration_source=self.source))
        self.assertFalse(target.exists()); self.assertEqual(self.source.read_bytes(), b'not a database')
        self.assertIn('migration', locations.read_record(self.record))
        self.source.write_bytes(before)
        locations.prepare(replace(self.settings, database_path=target, migration_source=self.source))
        self.assertTrue(target.exists())

    def test_interrupted_record_commit_resumes_verified_copy(self):
        self.select(); target = self.target_root/self.source.name
        original = locations.write_record
        def fail_final(path, data):
            if Path(path) == self.record and 'migration' not in data: raise OSError('模拟写回路径失败')
            return original(path, data)
        settings = replace(self.settings, database_path=target, migration_source=self.source)
        with mock.patch.object(locations, 'write_record', side_effect=fail_final):
            with self.assertRaises(OSError): locations.prepare(settings)
        self.assertTrue(target.is_file()); self.assertIn('migration', locations.read_record(self.record))
        locations.prepare(settings)
        self.assertNotIn('migration', locations.read_record(self.record)); self.assertTrue(self.source.is_file())

    def test_missing_source_does_not_start_empty_database(self):
        self.select(); self.source.unlink(); target = self.target_root/self.source.name
        with self.assertRaises(ValueError): locations.prepare(replace(self.settings, database_path=target, migration_source=self.source))
        self.assertFalse(target.exists())

    def test_relative_system_disk_and_non_writable_targets_rejected(self):
        with self.assertRaises(ValueError): locations.directory('relative/path')
        if os.name == 'nt':
            with mock.patch.dict(os.environ, {'SystemDrive': self.root.drive}):
                with self.assertRaises(ValueError): locations.directory(self.root/'forbidden')
        with mock.patch.object(locations, 'writable', side_effect=PermissionError('readonly')):
            with self.assertRaises(PermissionError): self.select()
        self.assertFalse(self.record.exists())

    def test_meme_library_files_are_not_overwritten_or_moved(self):
        (self.memes/'custom').mkdir(parents=True); path = self.memes/'custom'/'owned.gif'; path.write_bytes(b'user-owned')
        locations.seed_memes(self.memes)
        self.assertEqual(path.read_bytes(), b'user-owned')
        self.assertEqual({p.name for p in self.memes.iterdir()}, {'custom'})

    def test_seeded_library_offers_chinese_meanings_and_later_user_category(self):
        from types import SimpleNamespace
        from aicompanion.bridge.media import QQMedia, image_bytes
        locations.seed_memes(self.memes)
        self.db.set_setting('qq_meme_root', str(self.memes))
        controller = SimpleNamespace(character=SimpleNamespace(id='deepseek-whale'))
        media = QQMedia(self.db, controller, clock=lambda: 1000)
        offer = media.offer('synthetic:before')
        expected = {'开心庆祝', '思考疑惑', '困了晚安', '惊讶被戳', '无语出错', '耐心等待'}
        self.assertEqual(set(offer.categories), expected)
        for category, (meaning, paths) in offer.categories.items():
            self.assertEqual(meaning, category)
            self.assertEqual(len(paths), 1); self.assertTrue(paths[0].is_file())
            # T14 按内容哈希认领原创来源；只改分类目录，不能重编码图片或改变文件名。
            original = Path(locations.__file__).resolve().parents[1]/'characters/deepseek-whale/sprites'/paths[0].name
            self.assertEqual(paths[0].read_bytes(), original.read_bytes())
        self.assertFalse((self.memes/'manifest.json').exists())
        # 真正走现有发送适配，确认中文分类可选且图片字节有效，不调用模型或 QQ。
        rendered = media.render('<meme name="开心庆祝"/>', 'complete', offer, 'synthetic:before')
        self.assertEqual(len(rendered['images']), 1); image_bytes(rendered['images'][0])
        user = self.memes/'用户新增分类'; user.mkdir()
        content = (self.memes/'开心庆祝'/'celebrate.png').read_bytes()
        (user/'custom.png').write_bytes(content)
        before = {p.relative_to(self.memes): p.read_bytes() for p in self.memes.rglob('*') if p.is_file()}
        locations.seed_memes(self.memes)
        self.assertEqual(before, {p.relative_to(self.memes): p.read_bytes() for p in self.memes.rglob('*') if p.is_file()})
        updated = media.offer('synthetic:after')
        self.assertEqual(set(updated.categories), expected | {'用户新增分类'})
        self.assertEqual(updated.categories['用户新增分类'][0], '用户新增分类')
        self.assertFalse((self.memes/'manifest.json').exists())

    def test_custom_database_filename_is_preserved(self):
        custom = self.root/'old'/'other.sqlite3'; self.source.rename(custom)
        plan = locations.plan_change(custom, self.target_root, self.memes, self.record)
        self.assertEqual(Path(plan['migration']['target']).name, 'other.sqlite3')
        self.assertEqual(plan['database_name'], 'other.sqlite3')

    def test_automatic_legacy_copy_preserves_custom_name_on_next_start(self):
        custom = self.source.with_name('custom-memory.sqlite3'); self.source.rename(custom)
        target = self.target_root/custom.name
        locations.prepare(replace(self.settings, database_path=target, migration_source=custom))
        self.assertEqual(locations.read_record(self.record)['database_name'], custom.name)
        with mock.patch.dict(os.environ, {'AICOMPANION_PATHS_FILE': str(self.record)}), mock.patch('aicompanion.config.load_dotenv'):
            restarted = load_settings()
        self.assertEqual(restarted.database_path, target)
        self.assertEqual(Database(restarted.database_path).recent_messages()[0]['content'], '合成聊天 4827')


class LocationConfigTests(StorageBase):
    def load(self, values=None, frozen=False):
        env = {'AICOMPANION_PATHS_FILE': str(self.record), 'SystemDrive': 'Z:', **(values or {})}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch('aicompanion.config.load_dotenv'), \
             mock.patch('sys.frozen', frozen, create=True), mock.patch('aicompanion.storage.locations.default_data_root', return_value=self.root/'default'):
            return load_settings()

    def test_selected_paths_override_legacy_database_environment(self):
        self.select(); settings = self.load({'DATABASE_PATH': str(self.source)})
        self.assertEqual(settings.database_path, self.target_root/self.source.name)
        self.assertEqual(settings.meme_directory, self.memes); self.assertEqual(settings.migration_source, self.source)

    def test_explicit_old_environment_kept_until_user_selects_directory(self):
        settings = self.load({'DATABASE_PATH': str(self.source)})
        self.assertEqual(settings.database_path, self.source); self.assertIsNone(settings.migration_source)

    @unittest.skipUnless(os.name == 'nt', 'Windows 系统盘迁移策略')
    def test_explicit_system_drive_source_is_planned_for_safe_copy(self):
        env = {'AICOMPANION_PATHS_FILE': str(self.record), 'DATABASE_PATH': str(self.source), 'SystemDrive': self.source.drive}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch('aicompanion.config.load_dotenv'), \
             mock.patch('aicompanion.storage.locations.default_data_root', return_value=Path('D:/synthetic-AICompanion-memory')):
            settings = load_settings()
        self.assertEqual(settings.database_path, Path('D:/synthetic-AICompanion-memory')/self.source.name)
        self.assertEqual(settings.migration_source, self.source)

    def test_selected_directory_does_not_require_another_default_drive(self):
        self.select()
        with mock.patch.dict(os.environ, {'AICOMPANION_PATHS_FILE': str(self.record)}), \
             mock.patch('aicompanion.config.load_dotenv'), mock.patch('aicompanion.storage.locations.default_data_root', side_effect=AssertionError('已有选择不应重找默认盘')):
            settings = load_settings()
        self.assertEqual(settings.database_path, self.target_root/self.source.name)

    def test_packaged_legacy_database_is_planned_for_copy_instead_of_empty_start(self):
        legacy = self.root/'local'/'AICompanion'/'data'/locations.DATABASE_NAME
        legacy.parent.mkdir(parents=True); legacy.write_bytes(b'only checking existence, no open')
        settings = self.load({'LOCALAPPDATA': str(self.root/'local')}, frozen=True)
        self.assertEqual(settings.migration_source, legacy)
        self.assertEqual(settings.database_path, self.root/'default'/locations.DATABASE_NAME)

    @unittest.skipUnless(os.name == 'nt', 'Windows 盘符默认策略')
    def test_system_install_defaults_to_d_and_never_silently_uses_c(self):
        self.assertEqual(locations.default_data_root('C:/Program Files/AICompanion', system_drive='C:', available=[Path('D:/')]), Path('D:/AICompanion/data'))
        self.assertEqual(locations.default_data_root('D:/Pets', system_drive='C:', available=[]), Path('D:/Pets/data'))
        with self.assertRaises(ValueError): locations.default_data_root('C:/Pets', system_drive='C:', available=[])

    def test_invalid_path_record_fails_instead_of_silently_falling_back(self):
        self.record.write_text('{broken', encoding='utf8')
        with self.assertRaises(ValueError): self.load()


class StoragePageTests(StorageBase):
    def test_path_write_failure_restores_existing_library_selection(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from aicompanion.ui.storage_page import StoragePage
        app = QApplication.instance() or QApplication([])
        original = str(self.root/'old-library'); self.db.set_setting('qq_meme_root', original)
        page = StoragePage(self.db, self.settings); page.memes.setText(str(self.memes))
        with mock.patch('aicompanion.ui.storage_page.write_record', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): page.save()
        self.assertEqual(self.db.get_setting('qq_meme_root'), original); self.assertFalse(self.record.exists())
        page.deleteLater(); app.processEvents()

    def test_page_saves_next_start_plan_without_reading_chat_contents(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from aicompanion.ui.storage_page import StoragePage
        app = QApplication.instance() or QApplication([])
        page = StoragePage(self.db, self.settings)
        self.assertFalse(page.save()); self.assertFalse(self.record.exists())
        page.memory.setText(str(self.target_root)); page.memes.setText(str(self.memes))
        with mock.patch.object(self.db, 'recent_messages', side_effect=AssertionError('不读聊天')):
            self.assertTrue(page.save())
        self.assertFalse((self.target_root/self.source.name).exists())
        self.assertEqual(self.db.get_setting('qq_meme_root'), str(self.memes))
        self.assertTrue(self.source.exists()); self.assertIn('migration', locations.read_record(self.record))
        reopened = StoragePage(self.db, self.settings)
        self.assertEqual(reopened.memory.text(), str(self.target_root))
        page.deleteLater(); reopened.deleteLater(); app.processEvents()

    def test_other_page_meme_change_is_persisted_without_reverting_it(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from aicompanion.ui.storage_page import StoragePage
        app = QApplication.instance() or QApplication([])
        page = StoragePage(self.db, self.settings)
        chosen = self.root/'another-library'; self.db.set_setting('qq_meme_root', str(chosen))
        self.assertTrue(page.save())
        self.assertEqual(locations.read_record(self.record)['meme_root'], str(chosen))
        self.assertNotIn('migration', locations.read_record(self.record))
        page.deleteLater(); app.processEvents()


if __name__ == '__main__': unittest.main()
