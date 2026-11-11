"""资产测试只用合成 PNG/GIF 与临时 SQLite。"""
from pathlib import Path
from contextlib import closing
import json, os, tempfile, unittest
from unittest import mock
from PySide6.QtCore import QBuffer,QIODevice
from PySide6.QtGui import QImage
from aicompanion.storage.database import Database
from aicompanion.expression.catalog import AssetCatalog

def png(color=0xff3366ff):
    image=QImage(20,20,QImage.Format.Format_RGB32);image.fill(color)
    out=QBuffer();out.open(QIODevice.OpenModeFlag.WriteOnly);image.save(out,'PNG')
    return bytes(out.data())

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=Database(self.root/'synthetic.sqlite3');self.db.initialize()
        self.library=self.root/'library';(self.library/'happy').mkdir(parents=True)
        self.db.set_setting('qq_meme_root',str(self.library));self.cat=AssetCatalog(self.db)
        self.path=self.library/'happy'/'a.png';self.path.write_bytes(png())
        self.meta=dict(author='合成作者',source='synthetic:test',license='user-owned',semantic='庆祝',character_id='deepseek-whale')
    def tearDown(self):self.temp.cleanup()
    def test_unknown_license_quarantined_and_approval_explicit(self):
        aid,_=self.cat.register(self.path,self.library)
        self.assertEqual(self.cat.get(aid)['review_status'],'pending_review')
        self.assertIsNone(self.cat.resolve(aid))
        with self.assertRaises(ValueError):self.cat.review(aid,author='x',source='x',license='unknown')
        self.cat.review(aid,**{k:self.meta[k] for k in ('author','source','license')})
        self.assertEqual(self.cat.resolve(aid)[0],png())
    def test_review_keeps_user_disable_choice(self):
        # Codex 审计（ee541d0）：审核 / 保存许可和启用是两件事，已审核但被停用的图保存后仍然停用
        aid,_=self.cat.register(self.path,self.library)
        keys={k:self.meta[k] for k in ('author','source','license')}
        self.cat.review(aid,**keys); self.assertEqual(self.cat.get(aid)['enabled'],1)     # 没表过态：默认启用
        self.cat.enable(aid,False); self.cat.review(aid,**keys)
        self.assertEqual((self.cat.get(aid)['review_status'],self.cat.get(aid)['enabled']),('approved',0))
        self.cat.review(aid,enabled=True,**keys); self.assertEqual(self.cat.get(aid)['enabled'],1)  # 显式传入才改变

    def test_review_of_pending_respects_disable_chosen_before_review(self):
        aid,_=self.cat.register(self.path,self.library); self.cat.enable(aid,False)
        self.cat.review(aid,**{k:self.meta[k] for k in ('author','source','license')})
        self.assertEqual((self.cat.get(aid)['review_status'],self.cat.get(aid)['enabled']),('approved',0))

    def test_review_of_legacy_disabled_approved_row_without_marker_stays_disabled(self):
        aid,_=self.cat.register(self.path,self.library,self.meta)
        with self.db.transaction() as c: c.execute("UPDATE expression_assets SET review_status='approved',enabled=0 WHERE asset_id=?",(aid,))
        self.cat.review(aid,**{k:self.meta[k] for k in ('author','source','license')})
        self.assertEqual(self.cat.get(aid)['enabled'],0)

    def test_hash_duplicate_id_stable_and_thumbnail(self):
        aid,_=self.cat.register(self.path,self.library,self.meta)
        other=self.library/'happy'/'renamed.png';other.write_bytes(png())
        duplicate,reused=self.cat.register(other,self.library,self.meta)
        self.assertEqual(aid,duplicate);self.assertTrue(reused)
        self.assertTrue(self.cat.get(aid)['thumbnail'])
        self.assertEqual(len(self.cat.rows()),1)
    def test_missing_changed_and_invalid_images(self):
        aid,_=self.cat.register(self.path,self.library,self.meta)
        self.path.write_bytes(png(0xffff0000));self.assertIsNone(self.cat.resolve(aid))
        self.path.write_bytes(b'not a picture')
        with self.assertRaises(ValueError):self.cat.register(self.path,self.library,self.meta)
        self.path.unlink();self.assertIsNone(self.cat.resolve(aid))
    def test_rebuild_preserves_disable_review_and_original(self):
        self.library.joinpath('manifest.json').write_text(json.dumps({'asset_defaults':self.meta}),encoding='utf-8')
        self.cat.scan();aid=self.cat.rows()[0]['asset_id'];self.cat.enable(aid,False)
        result=self.cat.rebuild()
        self.assertEqual(result['reused'],1);self.assertFalse(self.cat.get(aid)['enabled'])
        self.assertEqual(self.path.read_bytes(),png())
    def test_import_nonoverwrite_and_non_system_root(self):
        with mock.patch.dict(os.environ,{'SystemDrive':'Z:'}):
            aid,_=self.cat.import_image(self.path,self.meta)
            again,reused=self.cat.import_image(self.path,self.meta)
        self.assertEqual(aid,again);self.assertTrue(reused)
        self.assertEqual(self.path.read_bytes(),png())
        self.assertTrue(self.cat.resolve(aid))
    def test_no_second_root_and_forbidden_sources(self):
        self.db.set_setting('qq_meme_root','')
        with self.assertRaises(ValueError):self.cat.import_image(self.path,self.meta)
        with self.assertRaises(ValueError):self.cat.register(self.path,self.library,dict(self.meta,source='Coopanion export'))
    def test_six_builtin_assets_and_cache_rebuild(self):
        self.db.set_setting('qq_meme_root','');self.assertEqual(self.cat.scan()['imported'],6)
        self.assertEqual(self.cat.stats()['approved'],6)
        aid=self.cat.rows()[0]['asset_id'];self.assertTrue(self.cat.resolve(aid))
        self.cat.clear_thumbnails();self.assertEqual(self.cat.stats()['thumbnail_bytes'],0)
        self.cat.rebuild();self.assertGreater(self.cat.stats()['thumbnail_bytes'],0)
    def test_v5_upgrade_backup_preserves_custom_settings(self):
        self.db.set_setting('qq_meme_root','user-selected')
        with self.db.transaction() as c:c.execute('PRAGMA user_version=5')
        self.db.initialize()
        self.assertEqual(self.db.get_setting('qq_meme_root'),'user-selected')
        self.assertTrue(list(self.root.glob('*.backup-*.sqlite3')))
        with self.db.transaction() as c:self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')

    def test_explicit_pending_license_stays_quarantined(self):
        aid,_=self.cat.register(self.path,self.library,dict(self.meta,review_status='pending_review'))
        self.assertIsNone(self.cat.resolve(aid));self.assertEqual(self.cat.get(aid)['review_status'],'pending_review')
    def test_bad_metadata_and_pack_escape_are_rejected(self):
        with self.assertRaises(ValueError):self.cat.register(self.path,self.library,dict(self.meta,intents='not a list'))
        manifest=self.root/'pack.json'
        manifest.write_text(json.dumps({'version':1,'assets':[{'file':'../outside.png','metadata':self.meta}]}),encoding='utf-8')
        self.assertEqual(self.cat.import_pack(manifest)['invalid'],1)
    def test_atomic_import_failure_leaves_source_and_retry_works(self):
        with mock.patch.dict(os.environ,{'SystemDrive':'Z:'}):
            if os.name=='nt':
                with mock.patch('aicompanion.expression.catalog.os.rename',side_effect=OSError('synthetic interrupted')):
                    with self.assertRaises(OSError):self.cat.import_image(self.path,self.meta)
                self.assertFalse(list((self.library/'memes/imported').iterdir()))
            self.assertTrue(self.cat.import_image(self.path,self.meta)[0])
        self.assertEqual(self.path.read_bytes(),png())
    def test_pack_import_is_repeatable_and_scan_does_not_duplicate(self):
        import hashlib
        manifest=self.root/'pack.json'
        manifest.write_text(json.dumps({'version':1,'assets':[{'file':'library/happy/a.png','metadata':self.meta,'sha256':hashlib.sha256(png()).hexdigest()}]}),encoding='utf-8')
        with mock.patch.dict(os.environ,{'SystemDrive':'Z:'}):
            self.assertEqual(self.cat.import_pack(manifest)['imported'],1)
            self.assertEqual(self.cat.import_pack(manifest)['reused'],1)
        self.cat.scan();self.assertEqual(len(self.cat.rows()),1)

    def test_asset_revision_preserves_previous_file_for_history(self):
        metadata=dict(self.meta,provider='synthetic',original_file_id='persistent-source-id')
        aid,_=self.cat.register(self.path,self.library,metadata);old=self.cat.get(aid)['revision']
        other=self.library/'happy/revision.png';other.write_bytes(png(0xff112233))
        same,_=self.cat.register(other,self.library,metadata)
        self.assertEqual(aid,same);self.assertIsNone(self.cat.resolve(aid))
        self.assertEqual(self.cat.resolve(aid,old,approved=False)[0],png())

    def test_actual_v5_fixture_upgrade_keeps_custom_content_and_backup(self):
        # 真实的 v5 旧库：默认角色当时是 emilia，夹具保持原样
        import sqlite3
        path=self.root/'actual-v5.sqlite3'
        with closing(sqlite3.connect(path)) as c, c:
            c.executescript((Path(__file__).parent/'fixtures/schema_v5.sql').read_text(encoding='utf-8'))
            c.execute("INSERT INTO characters VALUES ('emilia','用户自定义名字','用户自定义设定','用户自定义风格','','')")
            c.execute("INSERT INTO chat_messages(role,content,channel,sender) VALUES ('user','合成历史','qq:group:30003','qq:20002')")
            c.execute("INSERT INTO memories(content,scope,fact_key) VALUES ('合成私下记忆','private','synthetic:private')")
            c.execute('INSERT INTO app_settings VALUES (?,?)',('qq_chat_card:emilia',json.dumps({'style':'自定义风格'})))
            c.execute('PRAGMA user_version=5')
        migrated=Database(path);migrated.initialize();migrated.initialize()
        self.assertEqual(migrated.characters()[0].name,'用户自定义名字')
        self.assertEqual(migrated.get_setting('qq_chat_card:emilia')['style'],'自定义风格')
        self.assertEqual(migrated.recent_messages(character_id='emilia',channel='qq:group:30003')[0]['content'],'合成历史')
        backup=next(self.root.glob('actual-v5.backup-*.sqlite3'))
        with closing(sqlite3.connect(backup)) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],5)
            self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
        with migrated.transaction() as c:
            self.assertEqual(c.execute("SELECT scope FROM memories WHERE fact_key='synthetic:private'").fetchone()[0],'private')
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],8)
            self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')

    def test_forbidden_provenance_rejected_before_reading_source(self):
        with mock.patch.dict(os.environ,{'SystemDrive':'Z:'}),mock.patch.object(Path,'open',side_effect=AssertionError('禁止读取素材')):
            with self.assertRaises(ValueError):self.cat.import_image(self.path,dict(self.meta,source='Coopanion export'))
        self.assertFalse((self.library/'memes').exists())
    def test_gallery_child_escape_rejected_before_write(self):
        real=Path.resolve;outside=self.root/'outside'
        def resolved(path,*args,**kwargs):
            return outside if path==self.library/'memes/imported' else real(path,*args,**kwargs)
        with mock.patch.dict(os.environ,{'SystemDrive':'Z:'}),mock.patch.object(Path,'resolve',autospec=True,side_effect=resolved):
            with self.assertRaises(ValueError):self.cat.import_image(self.path,self.meta)
        self.assertFalse(outside.exists());self.assertFalse((self.library/'memes').exists())


class OriginalSpriteCopyTests(unittest.TestCase):
    """T13 新建图库会把仓库自带的 6 张姿态图复制进用户目录：按内容哈希认出来、按原创通过；同目录的其他图仍待审核。"""
    setUp = CatalogTests.setUp; tearDown = CatalogTests.tearDown
    def test_copied_original_sprites_are_approved_by_content_only(self):
        import shutil
        sprites = self.cat.builtin_root
        (self.library/'happy').mkdir(exist_ok=True)
        shutil.copyfile(sprites/'celebrate.png', self.library/'happy'/'celebrate.png')
        self.path.write_bytes(png(0xff123456))                       # 同目录里用户自己的图
        self.cat.scan()
        rows = {r['relative_path']: r for r in self.cat.rows()}
        original = rows['happy/celebrate.png']
        self.assertEqual((original['review_status'], original['metadata']['license']), ('approved', 'project-original'))
        self.assertEqual(original['metadata']['semantic'], '开心、庆祝、赞同')
        own = rows[self.path.relative_to(self.library).as_posix()]
        # 负责人 2026-11-11：用户自己放进图库的图默认当作自己的图直接可用，含义排队自动识别
        self.assertEqual((own['review_status'], own['enabled'], own['metadata']['license']), ('approved', 1, 'user-owned'))
        self.assertEqual(own['metadata']['analysis'], 'pending'); self.assertNotIn('analysis', original['metadata'])

    def copy_pending(self):
        import shutil
        (self.library/'happy').mkdir(exist_ok=True)
        target = self.library/'happy'/'celebrate.png'; shutil.copyfile(self.cat.builtin_root/'celebrate.png', target)
        aid, _ = self.cat.register(target, self.library, {'category': 'happy'})     # 旧版扫描：无许可 → 待审核
        return aid

    def test_explicit_disable_and_custom_meaning_survive_recognition(self):
        # Codex 审计（0a2b1d6）：识别出原创图时只补来源 / 许可，不覆盖用户的停用选择和自定义说明
        aid = self.copy_pending()
        self.cat.annotate(aid, {'semantic': '仅作纪念，不要发送', 'intents': ['纪念'], 'emotions': ['平静']})
        self.cat.enable(aid, True); self.cat.enable(aid, False)
        self.cat.scan(); row = self.cat.get(aid)
        self.assertEqual((row['review_status'], row['enabled']), ('approved', 0))
        self.assertEqual(row['metadata']['semantic'], '仅作纪念，不要发送'); self.assertEqual(row['metadata']['intents'], ['纪念'])
        self.assertEqual(row['metadata']['license'], 'project-original')

    def test_explicit_enable_before_recognition_is_kept(self):
        aid = self.copy_pending(); self.cat.enable(aid, True)
        self.cat.scan(); self.assertEqual(self.cat.get(aid)['enabled'], 1)

    def test_default_install_and_old_pending_without_choices_are_enabled(self):
        aid = self.copy_pending(); self.cat.scan(); row = self.cat.get(aid)
        self.assertEqual((row['review_status'], row['enabled'], row['metadata']['semantic']), ('approved', 1, '开心、庆祝、赞同'))

    def test_user_change_during_scan_is_serialised(self):
        # 扫描线程和用户在图库页停用同时发生：两者在写事务里串行，停用不会被识别覆盖
        import threading
        aid = self.copy_pending(); barrier = threading.Barrier(2)
        original_apply = self.cat._approve_original
        def slow(asset_id, original):
            barrier.wait(); original_apply(asset_id, original)
        self.cat._approve_original = slow
        worker = threading.Thread(target=self.cat.scan); worker.start()
        barrier.wait(); self.cat.enable(aid, False); worker.join(5)
        row = self.cat.get(aid)
        self.assertEqual(row['review_status'], 'approved'); self.assertEqual(row['enabled'], 0)

    def legacy_pending(self, **legacy):
        """合成 0a2b1d6 及更早版本的待审核记录：用户改过说明，但还没有 user_annotated 标记。"""
        aid = self.copy_pending()
        with self.db.transaction() as c:
            meta = json.loads(c.execute('SELECT metadata FROM expression_assets WHERE asset_id=?', (aid,)).fetchone()[0])
            meta.update(legacy); meta.pop('user_annotated', None)
            c.execute('UPDATE expression_assets SET metadata=? WHERE asset_id=?', (json.dumps(meta, ensure_ascii=False), aid))
        return aid

    def test_legacy_custom_annotation_without_marker_survives(self):
        # Codex 审计（ee541d0）：旧版用户标注没有 user_annotated，识别原创图时也不能被内置说明覆盖
        aid = self.legacy_pending(semantic='旧版用户标注：只留作纪念', intents=['纪念'], emotions=['平静'])
        self.cat.scan(); row = self.cat.get(aid)
        self.assertEqual(row['review_status'], 'approved'); self.assertEqual(row['metadata']['license'], 'project-original')
        self.assertEqual((row['metadata']['semantic'], row['metadata']['intents'], row['metadata']['emotions']),
                         ('旧版用户标注：只留作纪念', ['纪念'], ['平静']))

    def test_legacy_meaning_equal_to_folder_name_is_kept(self):
        # Codex 审计（e28c1ac）：和目录名相同不能证明用户没改过。重放 0a2b1d6 的旧 annotate（不写 user_annotated）：
        # 用户把含义改成和自定义目录同名的"仅作纪念"，识别后仍保留；空字段才补内置说明
        import shutil
        folder = self.library/'仅作纪念'; folder.mkdir()
        target = folder/'celebrate.png'; shutil.copyfile(self.cat.builtin_root/'celebrate.png', target)
        aid, _ = self.cat.register(target, self.library, {'category': '仅作纪念'})      # 旧版扫描：无许可、无默认值标记
        with self.db.transaction() as c:                                                   # 旧版 annotate：只改字段
            meta = json.loads(c.execute('SELECT metadata FROM expression_assets WHERE asset_id=?', (aid,)).fetchone()[0])
            meta['semantic'] = '仅作纪念'
            c.execute('UPDATE expression_assets SET metadata=? WHERE asset_id=?', (json.dumps(meta, ensure_ascii=False), aid))
        self.cat.scan(); meta = self.cat.get(aid)['metadata']
        self.assertEqual(self.cat.get(aid)['review_status'], 'approved'); self.assertEqual(meta['license'], 'project-original')
        self.assertEqual(meta['semantic'], '仅作纪念'); self.assertEqual(meta['intents'], ['开心', '庆祝', '赞同'])

    def test_first_scan_keeps_manifest_annotations_of_original_copy(self):
        # Codex 审计（e28c1ac）：首次扫描时清单写明的说明 / 用法 / 情绪不能被内置说明覆盖，只补来源和许可
        import shutil
        folder = self.library/'自定义收藏'; folder.mkdir()
        shutil.copyfile(self.cat.builtin_root/'celebrate.png', folder/'celebrate.png')
        (self.library/'manifest.json').write_text(json.dumps({'categories': {'自定义收藏': {
            'description': '仅作纪念，不要发送', 'asset_metadata': {'intents': ['纪念'], 'emotions': ['平静']}}}}, ensure_ascii=False), encoding='utf-8')
        self.cat.scan()
        row = next(r for r in self.cat.rows() if r['relative_path'] == '自定义收藏/celebrate.png'); meta = row['metadata']
        self.assertEqual((meta['semantic'], meta['intents'], meta['emotions']), ('仅作纪念，不要发送', ['纪念'], ['平静']))
        self.assertEqual((row['review_status'], meta['license'], meta['category']), ('approved', 'project-original', '自定义收藏'))

    def test_first_scan_partial_manifest_fills_only_unwritten_fields(self):
        import shutil
        folder = self.library/'开心庆祝'; folder.mkdir()
        shutil.copyfile(self.cat.builtin_root/'celebrate.png', folder/'celebrate.png')
        (self.library/'manifest.json').write_text(json.dumps({'categories': {'开心庆祝': {'asset_metadata': {'intents': ['纪念']}}}},
                                                             ensure_ascii=False), encoding='utf-8')
        self.cat.scan()
        meta = next(r for r in self.cat.rows() if r['relative_path'] == '开心庆祝/celebrate.png')['metadata']
        self.assertEqual((meta['semantic'], meta['intents'], meta['emotions']), ('开心、庆祝、赞同', ['纪念'], ['happy']))

    def test_previously_pending_copy_is_upgraded_but_rejected_is_kept(self):
        import shutil
        (self.library/'happy').mkdir(exist_ok=True)
        target = self.library/'happy'/'celebrate.png'; shutil.copyfile(self.cat.builtin_root/'celebrate.png', target)
        aid, _ = self.cat.register(target, self.library, {'category': 'happy'})     # 旧版扫描：无许可 → 待审核
        self.assertEqual(self.cat.get(aid)['review_status'], 'pending_review')
        self.cat.scan(); self.assertEqual(self.cat.get(aid)['review_status'], 'approved')
        with self.db.transaction() as c: c.execute("UPDATE expression_assets SET review_status='rejected',enabled=0 WHERE asset_id=?", (aid,))
        self.cat.scan(); self.assertEqual(self.cat.get(aid)['review_status'], 'rejected')
