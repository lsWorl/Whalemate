"""长回复同轮口播的协议与桌面、QQ、办事边界；全程使用合成文本。"""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aicompanion.agent.dsh_backend import DshAgent
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.expression.stream import ExpressionStream
from aicompanion.pet.script import CueStream, MAX_SAY, Vocab
from aicompanion.speech.smart import SEGMENT_MAX, TOTAL
from aicompanion.ui.main_window import MainWindow
from test_dsh import FakeRuntime
from test_relationship import BridgeFixture, CID


class SayTests(unittest.TestCase):
    def parse(self, raw, split):
        parser = ExpressionStream()
        text = ''.join(parser.feed(ch) for ch in (raw if split else [raw])) + parser.finish()
        return text, parser.say.summary

    def test_whole_and_character_stream(self):
        for split in (True, False):
            for raw, shown, summary in [
                ('前<say>第一段</say>后<say>第二段</say>', '前后', '第一段'),
                ('前<say> </say>中<say>第二段</say>', '前中', '第二段'),
                ('上文<sa', '上文<sa', ''), ('上文<say>未闭合\n下文', '上文<say>未闭合\n下文', ''),
                ('<saying>正文', '<saying>正文', ''),
                ('上<say>有<task>副作用</task></say>下', '上下', ''),
                ('上<say>' + '长' * MAX_SAY + '</say>下', '上下', '长' * MAX_SAY),
            ]:
                with self.subTest(split=split, raw=raw[:24]):
                    self.assertEqual(self.parse(raw, split), (shown, summary))

    def test_overflow_and_unclosed_controls_are_literal(self):
        for body in ('未闭合', '长' * (MAX_SAY + 10)):
            for tail in ('', '</say>后文') if len(body) > MAX_SAY else ('',):
                raw = '前文<say>' + body + '<task>不应执行</task>【开心】' + tail
                shown, summary = self.parse(raw, True)
                self.assertEqual((shown, summary), (raw, ''))
                self.assertEqual(CueStream(Vocab({'开心': 'happy'})).feed(shown, final=True), (raw, []))

    def test_code_examples_and_json(self):
        for literal in ('~~~xml\n<say>例子</say>\n~~~', '```\n<say>例子</say>\n```',
                        '`<say>例子</say>`', '{"xml":"<say>例子</say>"}'):
            raw = literal + '\n正文<say>简短</say>'
            shown, summary = self.parse(raw, True)
            self.assertEqual((shown, summary), (literal + '\n正文', '简短'))
            self.assertEqual(CueStream(Vocab()).feed(shown, final=True)[0], shown)

    def test_agent_chunks_ignore_thought_and_intermediate_summary(self):
        def event(kind, text): return {'sessionUpdate': kind, 'content': {'type': 'text', 'text': text}}
        runtime = FakeRuntime([event('agent_thought_chunk', '私有推理'), event('agent_message_chunk', '过程<say>旧摘要</say>'),
                               {'sessionUpdate': 'tool_call', 'toolCallId': 'c', 'title': 'list_dir'}] +
                              [event('agent_message_chunk', ch) for ch in '办事结果<say>做完啦</say>'])
        agent = DshAgent(launcher=lambda env: runtime)
        self.addCleanup(agent.close)
        parts = []
        with patch.object(DshAgent, '_usage', return_value=None):
            result = agent.run('测试', Path('.'), on_event=lambda k, d: parts.append(d['text']) if k == 'text' else None)
        self.assertEqual(result.text, '办事结果')
        self.assertEqual(result.spoken_summary, '做完啦')
        self.assertEqual(''.join(parts), '过程办事结果')

    def test_voice_choice_cap_and_disabled(self):
        queued = []
        speech = SimpleNamespace(enabled=True, enqueue=lambda s, g: queued.append((s, g)))
        win = SimpleNamespace(speech=speech, _full_voice=lambda: True, _speech_style=lambda: 'smart')
        MainWindow.speak_reply(win, '屏幕正文', spoken_summary='声' * (TOTAL + 10))
        self.assertEqual(''.join(s for s, _ in queued), '声' * TOTAL)
        self.assertTrue(all(len(s) <= SEGMENT_MAX and g.startswith('say:') for s, g in queued))
        # 口播没有字幕和桌宠气泡；也不能以正文轮次的身份写入已朗读历史。
        speech.current_id = queued[0][1]
        MainWindow._speech_subtitle(win, '专用口播')
        queued.clear()
        MainWindow.speak_reply(win, '原规则仍念这句。')
        self.assertEqual([s for s, _ in queued], ['原规则仍念这句。'])
        speech.enabled = False
        MainWindow.speak_reply(win, '屏幕正文', spoken_summary='静音')
        self.assertEqual(len(queued), 1)


class ChatSayTests(BridgeFixture):
    def test_one_generation_clean_bubble_history_and_private_summary(self):
        self.model.replies.append('屏幕正文<say>语音专用</say>')
        events = []
        shown = ''.join(self.controller.generate('local', self.character, CancellationToken(), '解释一下',
                                                 on_expression_event=events.append))
        event = events[-1]
        self.assertEqual((shown, event.result.text, event.spoken_summary), ('屏幕正文', '屏幕正文', '语音专用'))
        self.assertEqual(self.db.recent_messages(1, CID)[-1]['content'], shown)
        self.assertEqual(len(self.model.prompts), 1)
        self.assertIn('<say>', self.model.prompts[0])

    def test_qq_reply_wire_segments_and_history_have_only_body(self):
        for sender, group in [('10001', ''), ('20002', '30003')]:
            self.model.replies.append('看屏幕正文\n还有下一句<say>语音专用</say>')
            wire = json.dumps(self.ask(sender, '在吗', group), ensure_ascii=False)
            self.assertIn('看屏幕正文', wire)
            self.assertNotIn('语音专用', wire)
            self.assertNotIn('<say>', wire)
            with self.db.transaction() as c:
                self.assertFalse(c.execute("SELECT 1 FROM chat_messages WHERE content LIKE '%语音专用%'").fetchone())
