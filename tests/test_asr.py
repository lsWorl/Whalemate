"""识别协议、凭据隔离与通话轮次测试；不录音、不联网。"""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from aicompanion.config import Settings
from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.speech.client import SpeechClient
from aicompanion.ui.main_window import MainWindow


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings('test', Path('unused'), offline=True, speech_api_key='dummy-zhipu',
            qwen_api_key='dummy-qwen', asr_provider='qwen')
        self.client = SpeechClient(self.settings)
        self.token = CancellationToken()
        self.pcm = bytes(3200)

    def test_native_payload_contains_only_audio_and_uses_qwen_key(self):
        with patch.object(self.client.qwen, '_read', return_value=b'{"output":{"text":"hello"}}') as read:
            self.assertEqual(self.client.transcribe(self.pcm, 16000, self.token), 'hello')
        request = read.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer dummy-qwen')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], self.settings.qwen_asr_model)
        self.assertEqual(payload['parameters'], {'format': 'wav', 'sample_rate': '16000'})
        messages = payload['input']['messages']
        self.assertEqual(len(messages), 1)
        uri = messages[0]['content'][0]['input_audio']['data']
        raw = base64.b64decode(uri.split(',', 1)[1], validate=True)
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.readframes(wav.getnframes()), self.pcm)
        self.assertNotIn(b'dummy-chat', request.data)
        self.assertNotIn(b'dummy-zhipu', request.data)

    def test_complete_sentence_and_empty_results(self):
        for output, expected in [({'text': '  你好  '}, '你好'), ({'text': ''}, ''),
            ({'sentence': {'sentence_end': True, 'text': 'おかえり'}}, 'おかえり')]:
            with self.subTest(output=output), patch.object(self.client.qwen, '_post', return_value=output):
                self.assertEqual(self.client.transcribe(self.pcm, 16000, self.token), expected)

    def test_invalid_and_unfinished_results_fail(self):
        for output in [{}, {'text': []}, {'text': 'x'*4001},
            {'text': 'partial', 'sentence': {'sentence_end': False}}]:
            with self.subTest(output=type(output)), patch.object(self.client.qwen, '_post', return_value=output):
                with self.assertRaises(RuntimeError): self.client.transcribe(self.pcm, 16000, self.token)

    def test_invalid_pcm_never_reaches_network(self):
        for pcm, rate in [(b'', 16000), (b'1', 16000), (self.pcm, 44100), (bytes(16000*60+2), 16000)]:
            with self.subTest(rate=rate, size=len(pcm)), patch.object(self.client.qwen, '_post') as post:
                with self.assertRaises(ValueError): self.client.transcribe(pcm, rate, self.token)
                post.assert_not_called()

    def test_direct_wav_rate_mismatch_is_rejected(self):
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(self.pcm)
        with patch.object(self.client.qwen, '_post') as post, self.assertRaises(ValueError):
            self.client.qwen.transcribe(raw.getvalue(), 24000, self.token)
        post.assert_not_called()

    def test_cancellation_before_and_after_request(self):
        self.token.cancel()
        with patch.object(self.client.qwen, '_post') as post, self.assertRaises(ReplyCancelled):
            self.client.transcribe(self.pcm, 16000, self.token)
        post.assert_not_called()
        token = CancellationToken()
        def cancel_response(*args):
            token.cancel()
            return {'text': 'late result'}
        with patch.object(self.client.qwen, '_post', side_effect=cancel_response), self.assertRaises(ReplyCancelled):
            self.client.transcribe(self.pcm, 16000, token)

    def test_provider_credentials_do_not_fallback(self):
        from dataclasses import replace
        for provider, changes in [('qwen', {'qwen_api_key': ''}), ('zhipu', {'speech_api_key': ''}), ('unknown', {})]:
            client = SpeechClient(replace(self.settings, asr_provider=provider, **changes))
            with self.subTest(provider=provider), self.assertRaises(ValueError): client.ensure_recognition_ready()

    def test_qwen_failure_does_not_retry_on_zhipu(self):
        with patch.object(self.client.qwen, 'transcribe', side_effect=RuntimeError('offline')), patch.object(self.client, '_request') as zhipu:
            with self.assertRaises(RuntimeError): self.client.transcribe(self.pcm, 16000, self.token)
        zhipu.assert_not_called()


class CallCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.window = SimpleNamespace(closing=False, call_active=True, call_epoch=3, user_speaking=False,
            asr_worker=None, asr_queue=[], asr_failures=0, controller=SimpleNamespace(character=SimpleNamespace(id='role')),
            coordinator=Mock(), workers=set(), _start=Mock(), submit=Mock(), _status=Mock(), _end_call=Mock(),
            quota=Mock(**{'check_silent.return_value': 'ok'}))
        self.window.coordinator.ready.return_value = True
        self.window.coordinator.begin.return_value = ('generation', 'query')

    def test_waits_for_speech_and_all_recognition(self):
        for field, value in [('user_speaking', True), ('asr_worker', object()), ('asr_queue', [b'pcm'])]:
            setattr(self.window, field, value)
            MainWindow._pump(self.window)
            self.window._start.assert_not_called()
            setattr(self.window, field, False if field=='user_speaking' else None if field=='asr_worker' else [])
        MainWindow._pump(self.window)
        self.window._start.assert_called_once_with('generation', 'query')

    def test_ignores_late_or_wrong_role_results(self):
        for epoch, role in [(2, 'role'), (3, 'another')]:
            MainWindow._recognized(self.window, epoch, role, 'hello')
            MainWindow._recognition_failed(self.window, epoch, role, 'error')
        self.window.call_active = False
        MainWindow._recognized(self.window, 3, 'role', 'hello')
        self.window.submit.assert_not_called()
        self.assertEqual(self.window.asr_failures, 0)

    def test_empty_recognition_does_not_create_chat(self):
        for text in ['', '  ', '…？！']:
            MainWindow._recognized(self.window, 3, 'role', text)
        self.window.submit.assert_not_called()

    def test_success_resets_failures_without_clearing_current_speech(self):
        self.window.asr_failures = 2; self.window.user_speaking = True
        MainWindow._recognized(self.window, 3, 'role', '你好')
        self.window.submit.assert_called_once_with('你好', 'voice')
        self.assertTrue(self.window.user_speaking)
        self.assertEqual(self.window.asr_failures, 0)

    def test_three_failures_pause_call(self):
        for _ in range(2): MainWindow._recognition_failed(self.window, 3, 'role', 'error')
        self.window._end_call.assert_not_called()
        MainWindow._recognition_failed(self.window, 3, 'role', 'error')
        self.window._end_call.assert_called_once()

    def test_synchronous_device_error_cannot_reactivate_call(self):
        self.window.call_active = False
        self.window.speech_client = Mock()
        self.window.database = Mock()
        self.window.microphone = Mock()
        self.window.call_button = Mock(); self.window.speak_toggle = Mock()
        self.window._configure_voice = Mock(); self.window._microphone_device = Mock(return_value='')
        # 模拟设备在 start 内同步发送断开信号，随后 start 正常返回。
        self.window.microphone.start.side_effect = lambda device: setattr(self.window, 'call_active', False)
        MainWindow._toggle_call(self.window)
        self.assertFalse(self.window.call_active)
        self.window.speak_toggle.setChecked.assert_not_called()

    def test_missing_key_does_not_open_microphone(self):
        self.window.call_active = False
        self.window.speech_client = Mock()
        self.window.speech_client.ensure_recognition_ready.side_effect = ValueError('missing key')
        self.window.microphone = Mock()
        MainWindow._toggle_call(self.window)
        self.window.microphone.start.assert_not_called()


if __name__ == '__main__': unittest.main()
