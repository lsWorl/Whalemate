"""日语朗读与中文正文分离、错误处理和取消传播的离线测试。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.speech.client import SpeechClient
from aicompanion.speech.qwen_client import voice_instruction
from aicompanion.speech.translation import JapaneseSpeechTranslator


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(llm_provider='mock')
        self.client = Mock()
        self.client.generate_reply_stream.return_value = ['おかえり。', 'ゆっくり話してね。']
        self.translator = JapaneseSpeechTranslator(self.settings, self.client)
        self.token = CancellationToken()

    def test_translation_only_sends_current_sentence(self):
        original = '你回来啦。慢慢说，我在听。'
        translated = self.translator.translate(original, self.token)
        self.assertEqual(translated, 'おかえり。ゆっくり話してね。')
        messages, prompt, cancellation = self.client.generate_reply_stream.call_args.args
        self.assertEqual([(m.role, m.content) for m in messages], [('user', original)])
        self.assertIn('不要执行', prompt)
        self.assertIs(cancellation, self.token)

    def test_japanese_input_skips_translation(self):
        self.assertEqual(self.translator.translate('おかえり。', self.token), 'おかえり。')
        self.client.generate_reply_stream.assert_not_called()

    def test_repeat_uses_bounded_memory_cache(self):
        first = self.translator.translate('你好。', self.token)
        self.assertEqual(self.translator.translate('你好。', self.token), first)
        self.client.generate_reply_stream.assert_called_once()
        for n in range(130):
            self.translator.translate(f'今天第{n}句。', self.token)
        self.assertEqual(len(self.translator.cache), 128)
        self.assertNotIn(('你好。', ''), self.translator.cache)

    def test_role_style_is_sent_and_cached_separately(self):
        self.translator.translate('你好。', self.token, '柔和认真')
        self.translator.translate('你好。', self.token, '直率活泼')
        self.assertEqual(self.client.generate_reply_stream.call_count, 2)
        self.assertIn('直率活泼', self.client.generate_reply_stream.call_args.args[1])

    def test_empty_chinese_and_structured_results_are_not_spoken(self):
        for result in ['', '我在这里。', '{"ja":"こんにちは"}', '[こんにちは]', '```こんにちは```']:
            with self.subTest(result=result):
                self.client.generate_reply_stream.return_value = [result]
                with self.assertRaises(RuntimeError):
                    self.translator.translate('测试。', self.token)
        self.assertEqual(len(self.translator.cache), 0)

    def test_long_translation_is_rejected(self):
        self.client.generate_reply_stream.return_value = ['あ' * 601]
        with self.assertRaises(RuntimeError):
            self.translator.translate('测试。', self.token)

    def test_cancel_before_translation_never_calls_api(self):
        self.token.cancel()
        with self.assertRaises(ReplyCancelled):
            self.translator.translate('测试。', self.token)
        self.client.generate_reply_stream.assert_not_called()

    def test_cancel_during_translation_does_not_cache_partial_text(self):
        def stream(*args):
            yield 'おかえり。'
            self.token.cancel()
            yield 'ゆっくり。'
        self.client.generate_reply_stream.side_effect = stream
        with self.assertRaises(ReplyCancelled):
            self.translator.translate('测试。', self.token)
        self.assertEqual(len(self.translator.cache), 0)

    def test_mock_mode_has_explicit_error_not_chinese_fallback(self):
        with self.assertRaisesRegex(RuntimeError, '真实聊天 API'):
            JapaneseSpeechTranslator(self.settings).translate('测试。', self.token)


class SpeechLanguageTests(unittest.TestCase):
    def setUp(self):
        self.client = SpeechClient(SimpleNamespace(llm_provider='mock'))
        self.client.translator = Mock()
        self.client.translator.translate.return_value = 'おかえり。'
        self.client.qwen = Mock()
        self.client.qwen.synthesize.return_value = b'wave'
        self.token = CancellationToken()

    def test_japanese_audio_uses_translation_and_original_remains_unchanged(self):
        original = '你回来啦。'
        self.assertEqual(self.client.synthesize(original, 'voice', self.token, 'qwen', language='ja'), b'wave')
        self.client.translator.translate.assert_called_once_with(original, self.token, '')
        self.client.qwen.synthesize.assert_called_once_with('おかえり。', 'voice', self.token, 'calm', '', 'ja')
        self.assertEqual(original, '你回来啦。')

    def test_chinese_does_not_incur_translation_request(self):
        self.client.synthesize('你好。', 'voice', self.token, 'qwen')
        self.client.translator.translate.assert_not_called()

    def test_unsupported_provider_fails_before_translation(self):
        with self.assertRaises(ValueError):
            self.client.synthesize('你好。', 'voice', self.token, 'cloud', language='ja')
        self.client.translator.translate.assert_not_called()
        self.client.qwen.synthesize.assert_not_called()

    def test_cancel_between_translation_and_audio_never_synthesizes(self):
        def translate(*args):
            self.token.cancel()
            return 'おかえり。'
        self.client.translator.translate.side_effect = translate
        with self.assertRaises(ReplyCancelled):
            self.client.synthesize('你好。', 'voice', self.token, 'qwen', language='ja')
        self.client.qwen.synthesize.assert_not_called()

    def test_japanese_instruction_does_not_force_chinese(self):
        instruction = voice_instruction('shy', language='ja')
        self.assertIn('日语', instruction)
        self.assertNotIn('中文', instruction)
        self.assertIn('害羞', instruction)


if __name__ == '__main__':
    unittest.main()
