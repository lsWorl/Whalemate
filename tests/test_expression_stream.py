import unittest
from aicompanion.expression.stream import ExpressionStream
ID='st_0123456789abcdef'
BEFORE=f'<sticker asset_id="{ID}" position="before_text"/>'
AFTER=f'<sticker asset_id="{ID}" position="after_text"/>'

class StreamTests(unittest.TestCase):
    def parse(self,text,chunks=None):
        stream=ExpressionStream([ID],{'happy':ID})
        clean=''.join(stream.feed(c) for c in (chunks if chunks is not None else text))+stream.finish()
        return clean,stream
    def test_every_split_before_after_and_image_only(self):
        for raw,expected,position in [(BEFORE+'\n好耶','\n好耶','before_text'),('好耶\n'+AFTER,'好耶\n','after_text'),(BEFORE,'','before_text')]:
            for n in range(len(raw)+1):
                text,stream=self.parse(raw,[raw[:n],raw[n:]])
                self.assertEqual(text,expected);self.assertEqual(stream.selection,(ID,position))
    def test_plain_json_markdown_and_quotes_are_literal(self):
        for raw in ['1<2 和【开心】<挥手>','{"message":'+repr(BEFORE).replace("'",'"')+'}',
                    '```xml\n'+BEFORE+'\n```','示例 `'+BEFORE+'`','> '+BEFORE]:
            text,stream=self.parse(raw);self.assertEqual(text,raw);self.assertIsNone(stream.selection)
    def test_valid_json_with_escaped_quotes(self):
        import json
        raw=json.dumps({'text':BEFORE},ensure_ascii=False)
        self.assertEqual(self.parse(raw)[0],raw);self.assertIsNone(self.parse(raw)[1].selection)
    def test_malformed_unknown_path_and_unfinished_keep_body(self):
        for tag in ['<sticker asset_id="../private" position="after_text"/>','<sticker nope/>','<sticker'+('x'*600)+'\n']:
            raw='正常文字'+tag+'\n后续文字'
            text,stream=self.parse(raw)
            self.assertIn('正常文字',text);self.assertIn('后续文字',text);self.assertNotIn('<sticker',text)
            self.assertIsNone(stream.selection)
        self.assertEqual(self.parse('保留<sticker asset_id="bad')[0],'保留')
    def test_invalid_candidate_and_multiple_never_send_twice(self):
        text,stream=self.parse(BEFORE+AFTER)
        self.assertEqual(text,'');self.assertEqual(stream.selection,(ID,'before_text'))
        self.assertIn('multiple_markers',stream.diagnostics)
        text,stream=self.parse('<sticker asset_id="st_unknown0000" position="before_text"/>'+BEFORE)
        self.assertIsNone(stream.selection)
    def test_after_trailing_text_and_late_before_are_invalid(self):
        for raw in ['正文'+BEFORE,'正文'+AFTER+'后文']:
            text,stream=self.parse(raw);self.assertIsNone(stream.selection);self.assertIn('正文',text)
    def test_legacy_is_same_single_selection(self):
        text,stream=self.parse('好耶<meme name="happy"/>'+AFTER)
        self.assertEqual(text,'好耶');self.assertEqual(stream.selection,(ID,'after_text'))
    def test_task_preserved_and_not_selected(self):
        raw='<task>处理 '+BEFORE+' 文件</task>'
        text,stream=self.parse(raw);self.assertEqual(text,raw);self.assertIsNone(stream.selection)
    def test_cancel_and_independent_generations(self):
        a=ExpressionStream([ID]);b=ExpressionStream([ID])
        a.feed('<sticker');a.cancel()
        self.assertEqual(a.feed(BEFORE),'');self.assertEqual(a.finish(),'');self.assertIsNone(a.selection)
        self.assertEqual(b.feed(BEFORE),'');self.assertEqual(b.selection,(ID,'before_text'))
    def test_empty_finish_idempotent_and_no_marker(self):
        text,stream=self.parse('普通回复');self.assertEqual(text,'普通回复')
        self.assertEqual(stream.finish(),'');self.assertIsNone(stream.selection)

    def test_long_control_resyncs_at_close_and_keeps_same_line_body(self):
        text,stream=self.parse('正文<sticker '+('x'*600)+'>正常尾巴')
        self.assertEqual(text,'正文正常尾巴');self.assertIsNone(stream.selection)
