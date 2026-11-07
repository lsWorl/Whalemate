"""台词标注解析：流式拆分、未知词保留、去标注。"""
import unittest

from aicompanion.pet.script import Cue, CueStream, Vocab, strip_cues

VOCAB = Vocab({'开心': 'happy', '害羞': 'shy'}, {'挥手': 'wave', '点头': 'nod'})


class CueStreamTests(unittest.TestCase):
    def feed_all(self, tokens):
        stream = CueStream(VOCAB); text, cues = '', []
        for token in tokens:
            t, c = stream.feed(token); text += t; cues += c
        t, c = stream.flush(); return text+t, cues+c

    def test_basic_and_split_tokens(self):
        text, cues = self.feed_all(['【开', '心】太好', '了！<挥', '手>再见'])
        self.assertEqual(text, '太好了！再见')
        self.assertEqual(cues, [Cue('expression', 'happy'), Cue('motion', 'wave')])

    def test_unknown_words_are_kept(self):
        text, cues = self.feed_all(['1<2 而且 3>2，', '【注意】别忘了'])
        self.assertEqual(text, '1<2 而且 3>2，【注意】别忘了'); self.assertEqual(cues, [])

    def test_unclosed_at_end_is_text(self):
        text, cues = self.feed_all(['好的【开'])
        self.assertEqual(text, '好的【开'); self.assertEqual(cues, [])

    def test_long_bracket_is_text(self):
        text, _ = self.feed_all(['<这是一段很长很长的尖括号里的内容并没有闭合'])
        self.assertTrue(text.startswith('<这是'))

    def test_empty_vocab_passthrough(self):
        stream = CueStream(Vocab())
        self.assertEqual(stream.feed('【开心】hi'), ('【开心】hi', []))

    def test_strip_and_prompt(self):
        self.assertEqual(strip_cues('【害羞】谢谢你<点头>', VOCAB), '谢谢你')
        prompt = VOCAB.prompt()
        self.assertIn('开心', prompt); self.assertIn('挥手', prompt)
        self.assertEqual(Vocab().prompt(), '')

    def test_whale_pack_vocab(self):
        from aicompanion.characters import PACKS_DIR, load_pack
        vocab = Vocab.from_pack(load_pack(PACKS_DIR/'deepseek-whale'))
        self.assertGreaterEqual(len(vocab.expressions), 9); self.assertGreaterEqual(len(vocab.motions), 8)


if __name__ == '__main__': unittest.main()


class TaskMarkerTests(unittest.TestCase):
    def test_task_marker_split_across_tokens(self):
        from aicompanion.pet.script import CueStream, Vocab
        stream = CueStream(Vocab())
        out, cues = [], []
        for token in ['好的，我来', '整理。<ta', 'sk>整理下载', '文件夹</task>']:
            t, c = stream.feed(token); out.append(t); cues += c
        t, c = stream.flush(); out.append(t); cues += c
        self.assertEqual(''.join(out), '好的，我来整理。')
        self.assertEqual([(c.kind, c.name) for c in cues], [('task', '整理下载文件夹')])

    def test_plain_angle_brackets_kept(self):
        from aicompanion.pet.script import Vocab, strip_cues
        self.assertEqual(strip_cues('1<2 且 a<b>c', Vocab()), '1<2 且 a<b>c')
        self.assertEqual(strip_cues('好<task>写草稿</task>', Vocab()), '好')
