"""桌面附件验收。只使用临时目录、合成文档和可计数的假识图接口。"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PySide6.QtCore import QObject, QMimeData, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from aicompanion.agent import attachments as files
from aicompanion.agent.attachment_text import document_text
from aicompanion.agent.tools_server import ToolBox, Task
from aicompanion.bridge.media import QQMedia
from aicompanion.storage.database import Database
from aicompanion.ui.main_window import MainWindow
from test_relationship import Base, BridgeFixture, CID


def sample_pdf(content):
    # 标准 PDF 字体与明确的文本绘制指令；不依赖 Windows 字体、Qt 平台插件或先前测试的应用设置。
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream']
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(b'%d 0 obj\n' % index + obj + b'\nendobj\n')
    start = len(data)
    data.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets:
        data.extend(b'%010d 00000 n \n' % offset)
    data.extend(b'trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n%d\n%%%%EOF\n' % start)
    return bytes(data)


class AttachmentTests(Base):
    def setUp(self):
        super().setUp()
        self.root = Path(self.temp.name)
        self.workspace = self.root/'workspace'
        self.workspace.mkdir()
        self.trash = self.root/'trash'
        self.source = self.root/'source.txt'
        self.source.write_text('early\nlate', encoding='utf-8')
        self.box = ToolBox(trash=self.trash)
        self.describe = Mock(return_value='识图读到的文字')
        self.stop = threading.Event()

    def register(self, *paths):
        run = self.db.add_agent_run(CID, '整理附件', workspace=self.workspace, attachments=files.selection(paths))
        return self.db.agent_attachments(run)

    def prepare(self, rows, resuming=False):
        return files.prepare(self.db, rows, resuming=resuming, secrets=[], cancelled=self.stop, describe=self.describe)

    def test_copies_same_name_resume_changed_original_and_recycle(self):
        other = self.workspace/self.source.name
        other.write_text('other', encoding='utf-8')
        rows = self.register(self.source, other)
        self.prepare(rows)
        run = rows[0]['run_id']
        copies = [Path(row['copy']) for row in rows]
        self.assertEqual(len(set(copies)), 2)
        rows = self.db.agent_attachments(run)
        self.assertEqual([r['sha256'] for r in rows], [hashlib.sha256(p.read_bytes()).hexdigest() for p in copies])
        self.assertEqual([r['original'] for r in rows], [str(self.source), str(other)])
        self.source.rename(self.root/'original-moved.txt')
        copies[0].write_text('已办了一半', encoding='utf-8')
        self.db = Database(self.db.database_path)
        self.db.initialize()  # 模拟桌宠重启，running → paused
        files.recycle_finished(self.db, self.trash)
        self.assertTrue(all(p.exists() for p in copies))
        self.assertIsNotNone(self.db.resume_agent_run(run, CID))
        previews, _ = self.prepare(self.db.agent_attachments(run), resuming=True)
        self.assertEqual(previews[copies[0]], '已办了一半')
        self.assertEqual(other.read_text(encoding='utf-8'), 'other')
        self.db.finish_agent_run(run, 'done')
        files.recycle_finished(self.db, self.trash)
        files.recycle_finished(self.db, self.trash)
        for row, copy in zip(self.db.agent_attachments(run), copies):
            self.assertFalse(copy.exists())
            self.assertTrue(Path(row['recycled']).is_file())
        self.assertEqual((self.root/'original-moved.txt').read_text(encoding='utf-8'), 'early\nlate')

    def test_import_and_resume_conflicts_never_overwrite_or_reread_original(self):
        rows = self.register(self.source)
        target = Path(rows[0]['copy'])
        target.parent.mkdir(parents=True)
        target.write_text('别人的文件', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.prepare(rows)
        with self.assertRaisesRegex(ValueError, '中断'):
            self.prepare(rows, resuming=True)
        self.db.finish_agent_run(rows[0]['run_id'], 'failed')
        files.recycle_finished(self.db, self.trash)
        self.assertEqual(target.read_text(encoding='utf-8'), '别人的文件')

    def test_reject_formats_traversal_link_secret_and_limits(self):
        for invalid in [self.root/'a.exe', self.root/'else'/'..'/self.source.name, self.root/'not-a-file.txt',
                        Path('relative.txt'), self.root/'bad.txt:stream.txt']:
            with self.subTest(invalid=invalid), self.assertRaises((OSError, ValueError)):
                files.selection([invalid])
        with self.assertRaisesRegex(ValueError, '私密'):
            files.selection([self.source], [self.root])
        with patch.object(files, 'FILE_LIMIT', 3), self.assertRaisesRegex(ValueError, '单个'):
            files.selection([self.source])
        with patch.object(files, 'BATCH_LIMIT', 4), self.assertRaisesRegex(ValueError, '合计'):
            files.selection([self.source])
        with self.assertRaisesRegex(ValueError, '最多'):
            files.selection([self.source]*(files.COUNT_LIMIT+1))
        with self.assertRaisesRegex(ValueError, '二进制'):
            document_text(self.source, b'ab\0cd')

    def test_migrate_v16_with_old_run_chat_and_backup(self):
        run = self.db.add_agent_run(CID, '旧任务')
        self.db.add_message('user', '旧聊天', CID)
        with self.db.transaction() as conn:
            conn.execute('DROP TABLE agent_attachments')
            conn.execute('PRAGMA user_version=16')
        self.db.initialize()
        self.assertEqual(self.db.agent_runs()[0]['goal'], '旧任务')
        self.assertEqual(self.db.recent_messages(1, CID)[0]['content'], '旧聊天')
        self.assertEqual(self.db.agent_attachments(run), [])
        self.assertTrue(list(self.root.glob('*.backup-*.sqlite3')))
        self.assertTrue(self.register(self.source))

    def test_junction_on_source_and_destination(self):
        if os.name != 'nt':
            (self.root/'linked').symlink_to(self.workspace, target_is_directory=True)
        else:
            subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(self.root/'linked'), str(self.workspace)],
                           check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, '链接'):
            files.selection([self.root/'linked'/'file.txt'])
        rows = self.register(self.source)
        dest = self.workspace/'attachments'
        if os.name == 'nt':
            subprocess.run(['cmd','/d','/c','mklink','/J',str(dest),str(self.root/'linked')],check=True,capture_output=True)
        else:
            dest.symlink_to(self.root, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, '链接'):
                self.prepare(rows)
        finally:
            for link in (dest, self.root/'linked'):
                link.unlink() if link.is_symlink() else link.rmdir()

    def test_approved_workspace_does_not_implicitly_approve_original(self):
        self.box.task = Task(allowed=[self.root], originals={self.source:self.workspace/'copy.txt'}, always={'command'})
        for tool, args in [('write_file', {'path': str(self.source), 'content': 'x'}),
                           ('edit_file', {'path': str(self.source), 'old_text': 'early', 'new_text':'x'}),
                           ('move_path', {'source':str(self.source), 'destination':str(self.workspace/'moved.txt')}),
                           ('delete_path', {'path': str(self.source)}),
                           ('run_powershell', {'command': 'Set-Content source.txt x','cwd':str(self.root)})]:
            with self.subTest(tool=tool):
                self.assertTrue(self.box.call(tool, args)[1])
        self.assertEqual(self.source.read_text(encoding='utf-8'), 'early\nlate')
        copy = self.box.task.originals[self.source]
        copy.write_text('副本', encoding='utf-8')
        self.assertIn('副本', self.box.call('read_file', {'path': str(self.source)})[0])
        self.box.approve = Mock(return_value='once')
        self.assertFalse(self.box.call('write_file', {'path':str(self.source),'content':'已同意'})[1])
        self.box.approve.assert_called_once()
        self.assertEqual(self.source.read_text(encoding='utf-8'), '已同意')

    def test_budget_and_pagination_single_long_line(self):
        self.source.write_text('开头'+'中'*9000+'结尾', encoding='utf-8')
        rows = self.register(self.source)
        previews, _ = self.prepare(rows)
        path = Path(rows[0]['copy'])
        prompt, report = files.prompt_for('任务', rows, previews, SimpleNamespace(context_window=1800, max_output=100))
        self.assertLessEqual(report.total, report.limit)
        self.assertGreater(report.sections['attachments'], 0)
        self.assertIn(str(path), prompt)
        self.assertIn('开头', prompt)
        self.assertNotIn(str(self.source), prompt)
        self.assertNotIn('结尾', prompt)
        with self.assertRaisesRegex(ValueError, '预算'):
            files.prompt_for('满'*1000, rows, previews, SimpleNamespace(context_window=1800, max_output=100))
        self.box.task = Task(copies={path})
        offset = 1
        while True:
            page, failed = self.box.call('read_file', {'path': str(path), 'offset': offset})
            self.assertFalse(failed)
            self.assertLessEqual(len(page), 3600)
            if '结尾' in page:
                break
            next_offset = int(re.search(r'offset=(\d+)', page)[1])
            self.assertGreater(next_offset, offset)
            offset = next_offset

    def test_documents_as_seen_by_read_file(self):
        app = QApplication.instance() or QApplication([])
        doc = self.root/'sample.docx'
        with ZipFile(doc, 'w') as book:
            book.writestr('word/document.xml', '<document><p><r><t>中文段落</t></r></p></document>')
        sheet = self.root/'sample.xlsx'
        with ZipFile(sheet, 'w') as book:
            book.writestr('xl/workbook.xml','<book xmlns:r="rels"><sheets><sheet name="账本" r:id="r1"/></sheets></book>')
            book.writestr('xl/_rels/workbook.xml.rels','<rels><r Id="r1" Target="worksheets/s1.xml"/></rels>')
            book.writestr('xl/sharedStrings.xml','<sst><si><t>苹果</t></si></sst>')
            book.writestr('xl/worksheets/s1.xml','<s><c r="A1" t="s"><v>0</v></c><c r="B1"><v>12</v></c></s>')
        pdf = self.root/'sample.pdf'
        pdf.write_bytes(sample_pdf(b'BT /F1 16 Tf 50 150 Td (Offline PDF) Tj ET'))
        rows = self.register(doc, sheet, pdf)
        self.prepare(rows)
        self.box.task = Task(copies={Path(r['copy']) for r in rows})
        for row, expected in zip(rows, ['中文段落','A1 苹果','Offline PDF']):
            page, failed = self.box.call('read_file', {'path': row['copy']})
            self.assertFalse(failed, page)
            self.assertIn(expected, page)

    def test_invalid_documents_and_scanned_pdf(self):
        from io import BytesIO
        doc = BytesIO()
        with ZipFile(doc, 'w') as book:
            book.writestr('word/document.xml', '<!DOCTYPE document [<!ENTITY x "X">]><document/>')
        with self.assertRaisesRegex(ValueError, '实体'):
            document_text(Path('a.docx'), doc.getvalue())
        with self.assertRaisesRegex(ValueError, '损坏'):
            document_text(Path('a.xlsx'), b'bad')
        app = QApplication.instance() or QApplication([])
        pdf = self.root/'scan.pdf'
        pdf.write_bytes(sample_pdf(b'0 0 0 rg 10 10 20 20 re f'))
        with self.assertRaisesRegex(ValueError, '扫描件'):
            document_text(pdf, pdf.read_bytes())

    def test_image_routing_and_uncertain_calls_do_not_replay(self):
        app = QApplication.instance() or QApplication([])
        image = self.root/'image.png'
        pic = QImage(8, 8, QImage.Format.Format_RGB32)
        pic.fill(0xff8899)
        self.assertTrue(pic.save(str(image)))
        rows = self.register(image)
        preview, captions = self.prepare(rows)
        self.describe.assert_called_once_with(image.read_bytes(), 'image/png')
        self.box.task = Task(copies=set(preview), captions=captions)
        self.assertIn('识图读到的文字', self.box.call('read_file', {'path':rows[0]['copy']})[0])
        self.prepare(self.db.agent_attachments(rows[0]['run_id']), resuming=True)
        self.describe.assert_called_once()
        with self.db.transaction() as c:
            c.execute("UPDATE agent_attachments SET caption=''")
        with self.assertRaisesRegex(ValueError, '未确认'):
            self.prepare(self.db.agent_attachments(rows[0]['run_id']), resuming=True)
        self.describe.assert_called_once()

    def test_desktop_vision_uses_selected_identity_and_only_public_output(self):
        media = QQMedia(self.db, Mock())
        with patch.object(media, '_vision_call', return_value='<think>隐藏</think>文字<think>未结束') as vision:
            self.assertEqual(media.describe_task_image(b'image', 'image/png', '读图', CID), '文字')
        media.controller.router.resolve.assert_called_once_with('vision', CID, 'vision')
        self.assertEqual(vision.call_args.args[3], 'agent')
        self.assertIn('data:image/png;base64,', vision.call_args.args[1][1]['image_url']['url'])

    def test_composer_to_agent_session_reads_copies_and_records_terminal_result(self):
        from aicompanion.agent.dsh_backend import DshResult
        from aicompanion.ui.agent_session import AgentSession
        from aicompanion.ui.chat_worker import TaskWorker
        self.db.set_setting('dsh_workspace',str(self.workspace))
        window = Mock(closing=False, database=self.db, tasks=set())
        window.controller.character = self.character
        window.agent_toggle.isChecked.return_value = True
        window.agent = session = AgentSession.__new__(AgentSession)
        QObject.__init__(session)
        session.window = window
        session.server = Mock()
        session.box = self.box
        session.dsh = Mock()
        session._prompt = Mock(return_value='整理文件')
        session.housekeep = lambda: files.recycle_finished(self.db,self.trash)
        session.worker = None
        session.bridge = Mock()
        window.controller.router.agent_target.return_value = SimpleNamespace(model_id='offline')
        window.attachments.paths = [str(self.source)]
        window.input_box.text.return_value = '列出内容'
        window.AGENT_PREFIXES = MainWindow.AGENT_PREFIXES
        window.STOP_WORDS = MainWindow.STOP_WORDS

        def harness(prompt, cwd, target, on_event):
            copy, = session.box.task.copies
            self.assertIn(str(copy), prompt)
            self.assertNotIn(str(self.source), prompt)
            self.assertIn('early', session.box.call('read_file', {'path':str(copy)})[0])
            return DshResult('done', '已整理')
        session.dsh.run.side_effect = harness
        with patch('aicompanion.ui.agent_session.problem',return_value=''), \
             patch('aicompanion.ui.agent_session.dependency_problem',return_value=''), \
             patch.object(TaskWorker, 'start', lambda worker: worker.function(worker.cancellation)):
            MainWindow._send_current_message(window)
        self.assertEqual(self.db.agent_runs()[0]['status'], 'done')
        first, = self.db.agent_attachments(self.db.agent_runs()[0]['id'])
        self.assertTrue(Path(first['recycled']).exists())
        window.input_box.clear.assert_called_once()
        self.assertEqual(self.db.recent_messages(1,CID)[0]['content'],'列出内容')

    def test_drop_only_local_files(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.source))])
        event = Mock()
        event.mimeData.return_value = mime
        window = SimpleNamespace(attachments=Mock())
        MainWindow.dropEvent(window,event)
        window.attachments.add.assert_called_once()
        self.assertEqual([Path(p) for p in window.attachments.add.call_args.args[0]], [self.source])
        event.acceptProposedAction.assert_called_once()


class AttachmentBridgeTests(BridgeFixture):
    def test_qq_files_do_not_create_or_offer_a_desktop_job(self):
        for group in ('30003',''):
            packet = dict(platform='qq',group_id=group,sender_id='10001',called=True,
                          bot_account_id='90000',files=[{'path':'desktop-only-test.txt'}])
            self.assertTrue(self.service.handle(packet)['silent'])
            self.model.replies.append('收到。')
            self.service.handle(dict(packet, text='帮忙看附件'))
        self.assertFalse(self.db.agent_runs())
        self.assertTrue(all('desktop-only-test' not in p for p in self.model.prompts))
