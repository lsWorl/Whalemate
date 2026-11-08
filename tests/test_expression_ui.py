"""真实 Qt 控件与 worker；所有图片、模型和数据库均为合成。"""
import os,base64,tempfile,time,unittest
from pathlib import Path
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint
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
