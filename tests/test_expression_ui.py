"""真实 Qt 控件与 worker；所有图片、模型和数据库均为合成。"""
import os,base64,json,tempfile,time,unittest
from unittest import mock
from pathlib import Path
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from aicompanion.ui.chat_view import ChatView
from aicompanion.ui.pet_bubble import BubbleStack
from aicompanion.ui.expression_image import ExpressionImage
from aicompanion.ui.library_page import LibraryPage
from aicompanion.ui.chat_worker import ChatWorker
from aicompanion.storage.database import Database
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.services.memory_service import MemoryService
from aicompanion.domain.character import DEFAULT_CHARACTER
import test_expression_catalog as fixtures
import test_expression_controller as controller_fixtures
GIF=base64.b64decode('R0lGODlhCAAIAIEAAAAA/wAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACAAAACwAAAAACAAIAAAIDwABCBxIsKDBgwgTKkwYEAAh+QQBCAABACwAAAAACAAIAIH/AAAAAAAAAAAAAAAIDwABCBxIsKDBgwgTKkwYEAA7')

class ExpressionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_png_and_gif_in_existing_message_bubble(self):
        view=ChatView();view.show()
        for placement in ('before_text','after_text'):
            handle=view.append('鲸娘','好耶')
            self.assertTrue(handle.setAttachment(fixtures.png(),'image/png',placement))
            index=handle.bubble.layout().indexOf(handle.bubble.image)
            self.assertEqual(index,0 if placement=='before_text' else 1)
            handle.setText('鲸娘: 文字仍可流式更新')
            self.assertEqual(handle.bubble.label.text(),'文字仍可流式更新')
        pure=view.append('鲸娘','')
        self.assertTrue(pure.setAttachment(GIF,'image/gif','before_text'))
        self.assertIsNotNone(pure.bubble.image.animation);self.assertEqual(pure.bubble.image.animation.frameCount(),2)
        self.assertFalse(pure.bubble.label.isVisible())
        view.close();view.deleteLater();self.app.processEvents()
    def test_multi_segment_reply_becomes_separate_bubbles(self):
        # 负责人要求：多段回复分成几条消息显示，不塞进一个气泡；表情包单独一个气泡
        view=ChatView();view.show()
        rows=lambda:[view.column.itemAt(i).widget() for i in range(1,view.column.count())]
        visible=lambda:[r for r in rows() if r.isVisible()]
        reply=view.reply('鲸娘','正在想…')
        self.assertTrue(reply.set_attachment(GIF,'image/gif','before_text'))   # 还没说话：占位气泡变成图片气泡
        reply.set_text('哇！\n\n真的假的\n一箱啊\n\n分我一半')
        texts=[h.bubble.label.text() for h in reply.texts]
        self.assertEqual(texts,['哇！','真的假的','一箱啊','分我一半'])                  # 2026-11-09：一句一行，每行一个气泡
        self.assertEqual(len(visible()),5);self.assertIs(visible()[0],reply.image.row)   # 图在最前
        reply.set_text('哇！')                                                          # 流式过程中段数变少：多余气泡隐藏
        self.assertEqual(len(visible()),2)
        reply.mark('[已打断]');self.assertEqual(reply.texts[0].bubble.label.text(),'哇！\n[已打断]')
        after=view.reply('鲸娘','正在想…');after.set_text('说完了\n\n再发张图')
        self.assertTrue(after.set_attachment(fixtures.png(),'image/png','after_text'))
        self.assertIs(visible()[-1],after.image.row)                                   # 图在最后
        hidden=view.reply('鲸娘','正在想…');hidden.set_text('一\n\n二');view.hide()
        hidden.mark('[已打断]');self.assertEqual(hidden.texts[1].bubble.label.text(),'二\n[已打断]')   # 面板关着时也能标记
        view.show()
        empty=view.reply('鲸娘','正在想…');empty.mark('[未能完成回复]')
        self.assertEqual(empty.texts[0].bubble.label.text(),'[未能完成回复]')
        view.close();view.deleteLater();self.app.processEvents()
    def test_bad_image_keeps_text(self):
        view=ChatView();handle=view.append('鲸娘','正常文字')
        self.assertFalse(handle.setAttachment(b'bad','image/png'))
        self.assertEqual(handle.bubble.label.text(),'正常文字');view.deleteLater();self.app.processEvents()
    def test_pet_bubble_image_and_cleanup(self):
        stack=BubbleStack()
        self.assertTrue(stack.say_image(GIF,'image/gif',QPoint(300,300),'image'))
        self.assertIsNotNone(stack.latest.image_label.animation)
        stack.fade_out();self.assertFalse(stack.isVisible());self.app.processEvents()
    def test_library_page_review_preview_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            (root/'happy').mkdir();(root/'happy/a.png').write_bytes(fixtures.png())
            db.set_setting('qq_meme_root',str(root))
            page=LibraryPage(db,'deepseek-whale');page.catalog.scan();page.refresh()
            self.assertEqual(page.table.rowCount(),1)
            # 负责人 2026-11-11：自己放进图库的图默认可用，不要求填作者、来源、含义
            self.assertEqual(page.catalog.stats()['approved'],1);self.assertIn('可用',page.table.item(0,2).text())
            self.assertEqual(page.table.item(0,3).text(),'自己的图（自用）')
            aid=page.catalog.rows()[0]['asset_id']
            page.table.selectRow(0);self.app.processEvents()
            page.enable_selected(False);self.assertEqual(page.catalog.get(aid)['enabled'],0)
            # 改来源不影响停用选择（Codex 审计 ee541d0）
            page.license.setCurrentIndex(page.license.findData('CC0-1.0'));page.set_license()
            self.assertEqual((page.catalog.get(aid)['metadata']['license'],page.catalog.get(aid)['enabled']),('CC0-1.0',0))
            page.enable_selected(True);self.assertEqual(page.catalog.get(aid)['enabled'],1)
            page.semantic.setText('庆祝');page.save_meaning()
            self.assertEqual(page.catalog.get(aid)['metadata']['semantic'],'庆祝')
            page.clear_cache();self.assertEqual(page.catalog.stats()['thumbnail_bytes'],0)
            # 收集别人发的表情包：开关和每日分析上限直接保存（用户 2026-11-09）
            self.assertTrue(page.collect.isChecked());page.collect.setChecked(False)
            self.assertFalse(db.get_setting('sticker_collect_enabled'))
            page.analysis_daily.setValue(30);self.assertEqual(db.get_setting('sticker_analysis_daily'),30)
            self.assertIn('收集来的表情',page.collect_status.text())
            page.deleteLater();self.app.processEvents()
    def test_library_batch_operations_and_pack_license(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            (root/'happy').mkdir()
            for n in range(3):(root/'happy'/f'{n}.png').write_bytes(fixtures.png(0xff000000+n*7919))
            db.set_setting('qq_meme_root',str(root))
            page=LibraryPage(db,'deepseek-whale');page.catalog.scan();page.refresh()
            try:
                self.assertEqual(page.table.rowCount(),3)
                page.table.selectAll();self.app.processEvents();self.assertEqual(len(page.selected_ids()),3)
                page.enable_selected(False)
                self.assertEqual(sum(r['enabled'] for r in page.catalog.rows()),0)
                self.assertEqual(len(page.selected_ids()),3)                                    # 操作后保持选中，方便接着操作
                page.enable_selected(True);self.assertEqual(sum(r['enabled'] for r in page.catalog.rows()),3)
                # 含义排队自动识别；重新识别会清掉手改标记
                self.assertTrue(all(r['metadata'].get('analysis')=='pending' for r in page.catalog.rows()))
                # 清单导入、没写许可的图：许可待确认，批量设来源后可用；需要署名的许可缺作者时给出提示
                pack=page.catalog.get(page.catalog.register(self._pack_image(root),root,{'from_pack':True})[0])
                page.refresh();self.assertIn('许可待确认',page.summary.text())
                row=next(r for r in range(page.table.rowCount()) if page.table.item(r,0).data(256)==pack['asset_id'])
                page.table.clearSelection();page.table.selectRow(row);self.app.processEvents()
                page.license.setCurrentIndex(page.license.findData('CC-BY-4.0'));page.set_license()
                self.assertIn('需要原作者',page.status.text())
                page.license.setCurrentIndex(page.license.findData('user-owned'));page.set_license()
                self.assertEqual(page.catalog.get(pack['asset_id'])['review_status'],'approved')
            finally:
                page.deleteLater();self.app.processEvents()
    @staticmethod
    def _pack_image(root):
        path=root/'happy'/'pack.png';path.write_bytes(fixtures.png(0xff9a9a9a));return path
    def test_library_auto_meaning_uses_vision(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            (root/'开心').mkdir();(root/'开心'/'a.png').write_bytes(fixtures.png())
            db.set_setting('qq_meme_root',str(root))
            from aicompanion.expression.catalog import AssetCatalog
            AssetCatalog(db).scan()
            result={'suitable':True,'meaning':'开心到转圈','use_when':['庆祝'],'emotions':['开心']}
            with mock.patch('aicompanion.bridge.media.QQMedia.vision_ready',return_value=True), \
                 mock.patch('aicompanion.bridge.media.QQMedia.analyze_sticker',return_value=result):
                page=LibraryPage(db,'deepseek-whale',controller=SimpleNamespace())
                try:
                    worker=page.analyzer;self.assertIsNotNone(worker);self.assertTrue(worker.wait(5000))
                    for _ in range(4):self.app.processEvents()
                    row=page.catalog.rows()[0]
                    self.assertEqual((row['metadata']['semantic'],row['metadata']['analysis']),('开心到转圈','done'))
                    self.assertEqual(page.table.item(0,1).text(),'开心到转圈')
                finally:
                    page.deleteLater();self.app.processEvents()
    def test_library_auto_meaning_respects_money_quota(self):
        # Codex 审计 PR #31 R2：图库入口和 QQ 后台一样检查金额额度；到限不调用模型，队列保留
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            (root/'开心').mkdir()
            for n in range(2):(root/'开心'/f'{n}.png').write_bytes(fixtures.png(0xff101010+n*4099))
            db.set_setting('qq_meme_root',str(root));db.set_setting('quota_daily_total',0.5)
            from aicompanion.expression.catalog import AssetCatalog
            AssetCatalog(db).scan()
            calls=[]
            def analyze(media,data,mime):   # 每次识图记账 0.3 元：第二张开始前已超 0.5 元
                calls.append(1);db.add_token_usage('qq','mock',1,1,cost=0.3);return {'suitable':True,'meaning':'开心'}
            with mock.patch('aicompanion.bridge.media.QQMedia.vision_ready',return_value=True), \
                 mock.patch('aicompanion.bridge.media.QQMedia.analyze_sticker',autospec=True,side_effect=analyze):
                db.add_token_usage('qq','mock',1,1,cost=0.3)
                page=LibraryPage(db,'deepseek-whale',controller=SimpleNamespace())
                try:
                    worker=page.analyzer;self.assertIsNotNone(worker);self.assertTrue(worker.wait(5000))
                    for _ in range(4):self.app.processEvents()
                    self.assertEqual(len(calls),1)
                    self.assertEqual(sorted(r['metadata']['analysis'] for r in page.catalog.rows()),['done','pending'])
                    self.assertFalse(page.analyze_soon());self.assertEqual(len(calls),1)   # 已到限：不再启动
                finally:
                    page.deleteLater();self.app.processEvents()
    def test_library_reanalyze_explains_why_it_waits_or_failed(self):
        # 实机反馈 2026-11-11：点“重新识别含义”后一直“识别含义中”，看不到原因。额度满了要直说；识图失败要显示原因，不能报成已识别
        from types import SimpleNamespace
        from datetime import date
        from aicompanion.bridge.media import QQMedia
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            (root/'开心').mkdir();(root/'开心'/'a.png').write_bytes(fixtures.png(0xff202020))
            db.set_setting('qq_meme_root',str(root))
            from aicompanion.expression.catalog import AssetCatalog
            AssetCatalog(db).scan()
            db.set_setting('sticker_analysis_daily',1);db.set_setting(f'sticker_analysis_count:{date.today().isoformat()}',1)
            calls=[]
            def analyze(media,data,mime):
                calls.append(1);return media._sticker_failed('识图模型的回答格式不对（缺少 suitable 判断或含义为空）')
            with mock.patch('aicompanion.bridge.media.QQMedia.vision_ready',return_value=True), \
                 mock.patch('aicompanion.bridge.media.QQMedia.analyze_sticker',autospec=True,side_effect=analyze):
                page=LibraryPage(db,'deepseek-whale',controller=SimpleNamespace())
                try:
                    self.assertIsNone(page.analyzer)
                    page.table.selectAll();self.app.processEvents();page.reanalyze()
                    self.assertIn('每天最多分析 1 张',page.status.text());self.assertIn('每天最多分析 1 张',page.summary.text())
                    self.assertEqual(calls,[])
                    db.set_setting('sticker_analysis_daily',5);page.reanalyze()
                    worker=page.analyzer;self.assertIsNotNone(worker);self.assertTrue(worker.wait(5000))
                    for _ in range(6):self.app.processEvents()
                    for _ in range(50):
                        if page.analyzer is None:break
                        page.analyzer.wait(100);self.app.processEvents()
                    self.assertEqual(len(calls),2)                                  # 失败一次接着再试一次，两次后停
                    self.assertIn('回答格式不对',page.status.text());self.assertNotIn('已识别',page.status.text())
                    self.assertEqual(page.catalog.rows()[0]['metadata']['analysis'],'failed')
                finally:
                    page.deleteLater();self.app.processEvents()
    def _vision_stops_mid_batch(self,first_call):
        """11 张待识别图，真实 LibraryPage / QQMedia / StickerCollector，只替换识图配置和 _vision_call。"""
        from types import SimpleNamespace
        from datetime import date
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            (root/'开心').mkdir()
            for n in range(11):(root/'开心'/f'{n}.png').write_bytes(fixtures.png(0xff000000+n*65793+17))
            db.set_setting('qq_meme_root',str(root))
            from aicompanion.expression.catalog import AssetCatalog
            AssetCatalog(db).scan()
            profile=SimpleNamespace(base_url='https://example.invalid/v1',model_id='mock-vision',config_revision=1)
            calls=[]
            def vision_call(media,profile,content,max_tokens,purpose):
                calls.append(1);return first_call(db)
            with mock.patch('aicompanion.bridge.media.QQMedia.profile',lambda self,purpose='vision':profile), \
                 mock.patch('aicompanion.bridge.media.QQMedia._vision_call',autospec=True,side_effect=vision_call):
                page=LibraryPage(db,'deepseek-whale',controller=SimpleNamespace())
                try:
                    for _ in range(50):
                        if page.analyzer is None:break
                        page.analyzer.wait(100);self.app.processEvents()
                    rows=page.catalog.rows()
                    return (len(calls),int(db.get_setting(f'sticker_analysis_count:{date.today().isoformat()}',0) or 0),
                            sorted(r['metadata'].get('analysis') for r in rows),sum(int(r['metadata'].get('attempts') or 0) for r in rows),
                            page.status.text())
                finally:
                    page.deleteLater();self.app.processEvents()
    def test_library_turning_vision_off_mid_batch_keeps_rest_pending(self):
        # Codex 复审 R5：首张请求期间关闭识图，后面 10 张不扣次数、不记失败，留着等恢复
        def first(db):
            db.set_setting('qq_vision_enabled',False)
            return '{"suitable": true, "meaning": "开心"}'
        calls,count,states,attempts,_=self._vision_stops_mid_batch(first)
        self.assertEqual((calls,count,attempts),(1,1,0))
        self.assertEqual(states,['done']+['pending']*10)
    def test_library_first_image_rejected_by_api_keeps_rest_pending(self):
        # Codex 复审 R5：首张被接口拒收图片（HTTP 400 → 标为不支持看图），首张照常记一次失败，其余不动
        def first(db):raise RuntimeError('HTTP 400 image not supported')
        calls,count,states,attempts,status=self._vision_stops_mid_batch(first)
        self.assertEqual((calls,count,attempts),(1,1,1))
        self.assertEqual(states,['pending']*11)
        self.assertIn('HTTP 400',status)
    def test_worker_orders_attachment_before_text_and_final_without_tts_tags(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Database(Path(temp)/'synthetic.sqlite3');db.initialize()
            char=replace(DEFAULT_CHARACTER,id='deepseek-whale',name='鲸娘');db.add_character(char)
            class SpokenModel(controller_fixtures.Model):
                def generate_reply_stream(self, *args):
                    yield from super().generate_reply_stream(*args)
                    yield '<say>语音专用</say>'
            model=SpokenModel();ctl=ChatController(db,MemoryService(db),model);ctl.character=char
            worker=ChatWorker(ctl,'generation',char,'嘿');events=[];worker.expression_event.connect(lambda g,p:events.append((g,p)))
            worker.start()
            # worker 不等待 UI 回调。先让后台完成读图，再在主线程排空信号，避免两个线程同时进入 Qt 图像插件。
            self.assertTrue(worker.wait(10000), '本地图片与模拟回复应在十秒内生成')
            self.app.processEvents()
            self.assertTrue(events);self.assertEqual(events[0][1][1].kind,'attachment_ready')
            text=''.join(p[1] for _,p in events if p[0]=='text')
            self.assertEqual(text,'好耶！');self.assertNotIn('sticker',text);self.assertEqual(model.calls,1)
            final = events[-1][1][1]
            self.assertEqual((final.kind, final.result.text), ('final', '好耶！'))
            self.assertFalse(any(p[1].spoken_summary for _,p in events if p[0]=='expression'))
            worker.deleteLater();self.app.processEvents()

    def finish_library_task(self,page):
        worker=page.worker
        self.assertIsNotNone(worker)
        self.assertTrue(worker.wait(5000),'合成图库任务应及时结束')
        for _ in range(4):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertIsNone(page.worker,'完成的线程引用应清空，后续导入不能访问已释放的 Qt 对象')

    @mock.patch.dict(os.environ,{'SystemDrive':'Z:'})
    def test_library_import_button_after_scan_and_repeated_import(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            db.set_setting('qq_meme_root',str(root/'library'))
            (root/'source.png').write_bytes(fixtures.png())
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'version':1,'assets':[{'file':'source.png','metadata':{
                'author':'合成作者','source':'synthetic:test','license':'user-owned','semantic':'庆祝'}}]}),encoding='utf-8')
            page=LibraryPage(db,'deepseek-whale')
            try:
                page.scan();self.finish_library_task(page)
                from PySide6.QtWidgets import QPushButton
                button=next(b for b in page.findChildren(QPushButton) if b.text()=='导入素材清单')
                with mock.patch('aicompanion.ui.library_page.QFileDialog.getOpenFileName',return_value=(str(manifest),'清单 (*.json)')):
                    button.click();self.finish_library_task(page)
                    self.assertEqual(page.table.rowCount(),1)
                    self.assertIn('新增 1',page.status.text())
                    button.click();self.finish_library_task(page)
                    self.assertEqual(page.table.rowCount(),1)
                    self.assertIn('重复 1',page.status.text())
            finally:
                page.deleteLater();self.app.processEvents()

    def test_library_failed_import_shows_reason_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            db.set_setting('qq_meme_root',str(root/'library'))
            page=LibraryPage(db,'deepseek-whale')
            bad=root/'bad.json';bad.write_text('{"version": 99}',encoding='utf-8')
            try:
                with mock.patch('aicompanion.ui.library_page.QFileDialog.getOpenFileName',return_value=(str(bad),'')):
                    page.pick_pack();self.finish_library_task(page)
                self.assertIn('素材清单格式不支持',page.status.text())
                page.scan();self.finish_library_task(page)
                self.assertIn('处理完成',page.status.text())
            finally:
                page.deleteLater();self.app.processEvents()

    def test_library_task_cleanup_with_main_window_owner(self):
        from PySide6.QtWidgets import QWidget,QDialog
        class Owner(QWidget):
            def __init__(self):super().__init__();self.tasks=set()
            def _task_finished(self,worker):
                self.tasks.discard(worker);worker.deleteLater()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=Database(root/'synthetic.sqlite3');db.initialize()
            db.set_setting('qq_meme_root',str(root/'library'))
            owner=Owner();settings=QDialog(owner);page=LibraryPage(db,'deepseek-whale',settings)
            try:
                for _ in range(2):
                    page.scan();self.assertEqual(len(owner.tasks),1)
                    self.finish_library_task(page)
                    self.assertFalse(owner.tasks)
                    self.assertIn('处理完成',page.status.text())
            finally:
                owner.deleteLater();self.app.processEvents()
