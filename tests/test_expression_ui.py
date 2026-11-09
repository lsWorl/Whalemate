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
        self.assertEqual(texts,['哇！','真的假的\n一箱啊','分我一半'])
        self.assertEqual(len(visible()),4);self.assertIs(visible()[0],reply.image.row)   # 图在最前
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
            self.assertEqual(page.table.rowCount(),1);page.table.selectRow(0);self.app.processEvents()
            page.author.setText('合成作者');page.source.setText('synthetic:test');page.license.setCurrentText('user-owned')
            page.intents.setText('庆祝,附和');page.approve()
            self.assertEqual(page.catalog.stats()['approved'],1)
            aid=page.catalog.rows()[0]['asset_id'];self.assertEqual(page.catalog.get(aid)['metadata']['intents'],['庆祝','附和'])
            # Codex 审计（ee541d0）：已审核但被停用的图，再点"保存标注和许可确认"仍然停用
            page.catalog.enable(aid,False);page.refresh();page.table.selectRow(0);self.app.processEvents()
            page.author.setText('合成作者');page.source.setText('synthetic:test');page.license.setCurrentText('user-owned')
            page.approve();self.assertEqual(page.catalog.get(aid)['enabled'],0)
            page.clear_cache();self.assertEqual(page.catalog.stats()['thumbnail_bytes'],0)
            page.deleteLater();self.app.processEvents()
    def test_worker_orders_attachment_before_text_and_final_without_tts_tags(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Database(Path(temp)/'synthetic.sqlite3');db.initialize()
            char=replace(DEFAULT_CHARACTER,id='deepseek-whale',name='鲸娘');db.add_character(char)
            model=controller_fixtures.Model();ctl=ChatController(db,MemoryService(db),model);ctl.character=char
            worker=ChatWorker(ctl,'generation',char,'嘿');events=[];worker.expression_event.connect(lambda g,p:events.append((g,p)))
            worker.start()
            deadline=time.monotonic()+10
            while worker.isRunning() and time.monotonic()<deadline:self.app.processEvents();time.sleep(.005)
            worker.wait();self.app.processEvents()
            self.assertTrue(events);self.assertEqual(events[0][1][1].kind,'attachment_ready')
            text=''.join(p[1] for _,p in events if p[0]=='text')
            self.assertEqual(text,'好耶！');self.assertNotIn('sticker',text);self.assertEqual(model.calls,1)
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
