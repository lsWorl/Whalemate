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
        # 无效的标记不占名额：后面有效的那张照常发（以前整轮作废，是"写错一次就没图"的原因之一）
        text,stream=self.parse('<sticker asset_id="st_unknown0000" position="before_text"/>'+BEFORE)
        self.assertEqual(stream.selection,(ID,'before_text'));self.assertIn('invalid_asset',stream.diagnostics)
    def test_late_before_and_mid_text_are_placed_after_not_dropped(self):
        text,stream=self.parse('正文'+BEFORE)
        self.assertEqual((text,stream.selection),('正文',(ID,'after_text')));self.assertIn('position_adjusted',stream.diagnostics)
        text,stream=self.parse('正文'+AFTER+'后文')
        self.assertEqual((text,stream.selection),('正文后文',(ID,'after_text')));self.assertIn('mid_text_marker',stream.diagnostics)
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

    def test_internal_history_annotation_all_token_splits(self):
        for label in ('[角色已发送表情：笑死]','[角色已发送表情:开心]'):
            raw='正常回复'+label+'正文保留'
            for n in range(len(raw)+1):
                text,stream=self.parse(raw,[raw[:n],raw[n:]])
                self.assertEqual(text,'正常回复正文保留')
                self.assertIsNone(stream.selection)
                self.assertIn('internal_annotation_echo',stream.diagnostics)

    def test_internal_annotation_after_real_marker_does_not_invalidate_image(self):
        text,stream=self.parse('好耶'+AFTER+'[角色已发送表情：开心]')
        self.assertEqual(text,'好耶');self.assertEqual(stream.selection,(ID,'after_text'))

    def test_ordinary_brackets_arrays_and_literal_examples_preserved(self):
        import json
        label='[角色已发送表情：开心]'
        for raw in ('[笑哭] 好消息','[]','[1, {"a": [2, 3]}]',json.dumps([label,BEFORE],ensure_ascii=False),
                    '```text\n'+label+'\n```','`'+label+'`','> '+label):
            text,stream=self.parse(raw)
            self.assertEqual(text,raw);self.assertIsNone(stream.selection)

    def test_incomplete_or_oversized_annotation_resynchronizes(self):
        for raw in ('正常[角色已发送表情：没闭合\n后续',
                    '正常[角色已发送表情：'+('x'*600)+']\n后续'):
            text,stream=self.parse(raw)
            self.assertEqual(text,'正常\n后续');self.assertIsNone(stream.selection)
        self.assertEqual(self.parse('正文[角色已发送表情：没闭合')[0],'正文')

    def test_explicit_text_only_choice_is_hidden_at_every_split(self):
        raw='<expression mode="text_only"/>好耶，今天也顺顺利利！'
        for n in range(len(raw)+1):
            text,stream=self.parse(raw,[raw[:n],raw[n:]])
            self.assertEqual(text,'好耶，今天也顺顺利利！');self.assertIsNone(stream.selection)
            self.assertIn('explicit_text_only',stream.diagnostics)

    def test_old_text_only_marker_is_hidden_and_does_not_block_a_real_image(self):
        text,stream=self.parse('<expression mode="text_only"/>正文'+AFTER)
        self.assertEqual(text,'正文');self.assertEqual(stream.selection,(ID,'after_text'))
        self.assertIn('explicit_text_only',stream.diagnostics)

    def test_expression_decision_literal_json_code_and_bad_syntax(self):
        import json
        tag='<expression mode="text_only"/>'
        for raw in (json.dumps({'example':tag}),'```xml\n'+tag+'\n```'):
            self.assertEqual(self.parse(raw)[0],raw)
        for tag in ('<expression bad/>','<expression mode="text_only"'):
            text,stream=self.parse('保留正文'+tag)
            self.assertEqual(text,'保留正文');self.assertIsNone(stream.selection)


class ShortHandleProtocolTests(unittest.TestCase):
    """2026-11-08 协议：本轮短编号、位置由出现处决定、写法宽松；历史写法只在含义和本轮候选一致时才算。"""
    def parse(self,raw,chunks=None):
        stream=ExpressionStream([ID],{},{'s1':ID},{'开心到笑出声':ID})
        text=''.join(stream.feed(c) for c in (chunks if chunks is not None else [raw]))+stream.finish()
        return text,stream

    def test_handle_position_from_location_every_split(self):
        for raw,expected,position in [('<sticker id="s1"/>好耶','好耶','before_text'),('好耶<sticker id="s1"/>','好耶','after_text'),
                                      ('<sticker id="s1"/>','','before_text')]:
            for n in range(len(raw)+1):
                text,stream=self.parse(raw,[raw[:n],raw[n:]])
                self.assertEqual((text,stream.selection),(expected,(ID,position)))

    def test_lenient_spellings(self):
        for tag in ('<sticker id=s1>','<sticker id="S1" />',"<sticker id='s1'/>",'<sticker s1/>',
                    '<sticker position="after_text" id="s1"/>','<sticker asset_id="s1"/>','<sticker asset_id="'+ID+'"/>'):
            text,stream=self.parse('好耶'+tag)
            self.assertEqual(text,'好耶',tag);self.assertEqual(stream.selection,(ID,'after_text'),tag)

    def test_cue_tags_do_not_count_as_visible_text(self):
        for raw in ('【开心】<sticker id="s1"/>好耶','<挥手><sticker id="s1"/>好耶'):
            text,stream=self.parse(raw)
            self.assertEqual(stream.selection,(ID,'before_text'),raw)

    def test_unknown_handle_and_out_of_range_are_hidden(self):
        for tag in ('<sticker id="s2"/>','<sticker id="s11"/>','<sticker id="st_other12345"/>','<sticker/>'):
            text,stream=self.parse('好耶'+tag)
            self.assertEqual(text,'好耶');self.assertIsNone(stream.selection)

    def test_history_echo_matches_current_meaning_only(self):
        text,stream=self.parse('<sticker sent="开心到笑出声"/>哈哈')
        self.assertEqual((text,stream.selection),('哈哈',(ID,'before_text')))
        text,stream=self.parse('哈哈<sticker sent="以前的另一张"/>')
        self.assertEqual(text,'哈哈');self.assertIsNone(stream.selection);self.assertIn('history_echo',stream.diagnostics)

    def test_markers_counted_for_observability(self):
        self.assertEqual(self.parse('纯文字')[1].markers,0)
        self.assertEqual(self.parse('好<sticker id="s9"/>')[1].markers,1)
        self.assertEqual(self.parse('好<sticker id="s1"/><sticker id="s1"/>')[1].markers,2)



class StrayEmotionTagTests(unittest.TestCase):
    """词表外的短情绪方括号不会触发动画、只会露在正文里：传入词表时隐藏它们；词表内的、【不说】、长内容保留。"""
    def parse(self, raw, chunks=None):
        stream = ExpressionStream([ID], {}, {'s1': ID}, {}, {'开心', '挥手'})
        text = ''.join(stream.feed(c) for c in (chunks if chunks is not None else [raw]))+stream.finish()
        return text, stream

    def test_stray_tags_hidden_at_every_split(self):
        raw = '冤枉啊【委屈】我没动'
        for n in range(len(raw)+1):
            text, stream = self.parse(raw, [raw[:n], raw[n:]])
            self.assertEqual(text, '冤枉啊我没动'); self.assertIn('stray_emotion_tag', stream.diagnostics)

    def test_vocab_silence_long_and_code_are_kept(self):
        for raw in ('【开心】好耶', '【不说】', '【注意：这是一段很长的说明】', '`【委屈】`', '```\n【委屈】\n```', '【开心'):
            self.assertEqual(self.parse(raw)[0], raw, raw)

    def test_without_vocab_nothing_changes(self):
        stream = ExpressionStream([ID])
        self.assertEqual(stream.feed('【委屈】')+stream.finish(), '【委屈】')

    def test_rejected_handle_recorded_for_observability(self):
        _, stream = self.parse('好<sticker id="s12"/><sticker id="编号"/><sticker id="合成住院细节"/>')
        self.assertEqual(stream.rejected, ['handle_out_of_range', 'placeholder', 'free_text'])


class MeaningAsIdTests(unittest.TestCase):
    def test_meaning_written_as_id_resolves_only_when_unique(self):
        other = 'st_fedcba9876543210'
        stream = ExpressionStream([ID, other], {}, {'s1': ID, 's2': other}, {'困到闭眼却还在工作': ID, '开心到转圈': other})
        self.assertEqual(stream.feed('晚安<sticker id="困到闭眼"/>')+stream.finish(), '晚安')
        self.assertEqual(stream.selection, (ID, 'after_text')); self.assertIn('meaning_as_id', stream.diagnostics)
        stream = ExpressionStream([ID, other], {}, {'s1': ID, 's2': other}, {'开心到转圈': ID, '开心到跳': other})
        stream.feed('<sticker id="开心"/>'); stream.finish()
        self.assertIsNone(stream.selection); self.assertEqual(stream.rejected, ['free_text'])   # 对得上两张：不猜；只记形态


class HtmlImgTagTests(unittest.TestCase):
    """第八轮验收：模型写了 `<img src="">` 想贴图，原样露在正文里。隐藏它；代码块、行内代码和普通的 <in…> 文字不受影响。"""
    def parse(self, raw, chunks=None):
        stream = ExpressionStream([ID], {}, {'s1': ID}, {})
        text = ''.join(stream.feed(c) for c in (chunks if chunks is not None else [raw]))+stream.finish()
        return text, stream

    def test_img_tags_hidden_at_every_split(self):
        raw = '烤红薯 <img src=""> 你就不想着给我留一口'
        for n in range(len(raw)+1):
            text, stream = self.parse(raw, [raw[:n], raw[n:]])
            self.assertEqual(text, '烤红薯  你就不想着给我留一口'); self.assertIn('html_img_tag', stream.diagnostics)
        self.assertEqual(self.parse('好<img/>')[0], '好'); self.assertEqual(self.parse('好<img>')[0], '好')

    def test_whitespace_variants_and_multiline_attributes_at_every_split(self):
        # Codex 审计：制表符 / 换行 / CRLF 跟在 img 后面也要隐藏，属性跨行时整段去掉，不能把 src 吐成正文
        for raw in ('你好<img\tsrc="合成地址">晚安', '你好<img\nsrc="合成地址">晚安', '你好<img\r\nsrc="合成地址"\r\nalt="x">晚安',
                    '你好<img src="a>b">晚安', '你好<IMG\n  src=x\n/>晚安'):
            for n in range(len(raw)+1):
                text, stream = self.parse(raw, [raw[:n], raw[n:]])
                self.assertEqual(text, '你好晚安', (raw, n)); self.assertIn('html_img_tag', stream.diagnostics)
        text, stream = self.parse('你好<img\nsrc="没闭合')
        self.assertEqual(text, '你好'); self.assertIn('html_img_tag', stream.diagnostics)

    def test_overlong_img_is_dropped_never_echoed(self):
        # Codex 审计（e28c1ac）：认出 <img 后超长也只丢弃，不能把标签前缀或 src 还回正文
        from aicompanion.expression.stream import IMG_LIMIT
        cases = []
        for n in (IMG_LIMIT-10, IMG_LIMIT, IMG_LIMIT+1, IMG_LIMIT+100):
            cases += ['你好<img src='+'x'*n+'>晚安', '你好<img src="'+'x'*n+'">晚安', "你好<img\r\nalt='"+'y'*n+"'\nsrc=z>晚安"]
        for raw in cases:
            text, stream = self.parse(raw)
            self.assertEqual(text, '你好晚安', len(raw)); self.assertIn('html_img_tag', stream.diagnostics)
        raw = '你好<img src="'+'x'*(IMG_LIMIT+50)+'">晚安'
        for n in (0, 3, 5, 8, 200, IMG_LIMIT, IMG_LIMIT+40, len(raw)-4, len(raw)-2, len(raw)):
            self.assertEqual(self.parse(raw, [raw[:n], raw[n:]])[0], '你好晚安', n)
        chunks = [raw[i:i+7] for i in range(0, len(raw), 7)]
        self.assertEqual(self.parse(raw, chunks)[0], '你好晚安')

    def test_unclosed_quote_in_img_does_not_swallow_everything(self):
        # 落单的引号：超过上限后不再认这个引号，遇到下一个 > 就结束，后面的正常文字保留
        from aicompanion.expression.stream import IMG_LIMIT
        raw = '你好<img src="'+'x'*(IMG_LIMIT+20)+'>晚安'
        text, stream = self.parse(raw)
        self.assertEqual(text, '你好晚安'); self.assertIn('html_img_overlong', stream.diagnostics)

    def test_code_and_ordinary_angle_text_kept(self):
        for raw in ('`<img src="a.png">`', '```html\n<img src="a.png">\n```', '```\n<img\tsrc="a">\n```', '<input> 和 <imgs> 都是普通文字', '1<img', 'a<i',
                    '{"html": "<img src=1>"}'):
            self.assertEqual(self.parse(raw)[0], raw, raw)

    def test_img_does_not_block_real_sticker(self):
        text, stream = self.parse('<img src="">好耶<sticker id="s1"/>')
        self.assertEqual((text, stream.selection), ('好耶', (ID, 'after_text')))
