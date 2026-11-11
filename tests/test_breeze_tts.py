"""Breeze Blue 语音合成：请求格式、错误处理、按角色的音色配置。不联网，只用本机端口。"""
import http.server
import io
import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.models import MODEL_PRESETS, PROVIDER_PRESETS, Binding
from aicompanion.providers.router import ModelRouter, TtsTarget
from aicompanion.providers.store import ModelConfigStore
from aicompanion.speech.breeze_client import BreezeSpeechClient, payload_for
from aicompanion.speech.client import SpeechClient
from aicompanion.storage.database import Database


def wav_bytes(rate=24000, frames=240):
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(b'\x01\x00' * frames)
    return out.getvalue()


def target(**overrides):
    values = dict(provider_kind='breezeblue', base_url='https://api.breeze.blue/v1', api_key='bz-secret', model_id='breeze-tts-2',
                  voice='voc_8rsb3nhb7645', language='zh', speed=1.0, volume=1.0, instructions='声音清亮', cache_id='c', label='x')
    values.update(overrides)
    return TtsTarget(**values)


class FakeProtector:
    name = 'fake'
    def protect(self, data): return data[::-1]
    def unprotect(self, data): return data[::-1]


class RequestTests(unittest.TestCase):
    def capture(self, result=None, error=None):
        sent = {}

        def fake_fetch(request, cancellation, timeout, limit):
            sent.update(url=request.full_url, headers={k.lower(): v for k, v in request.header_items()},
                        body=json.loads(request.data), method=request.get_method())
            if error: raise error
            return result if result is not None else wav_bytes()
        return BreezeSpeechClient(fake_fetch), sent

    def test_request_follows_official_shape(self):
        client, sent = self.capture()
        audio = client.synthesize(target(), '你好呀', CancellationToken(), mood='happy')
        self.assertTrue(audio.startswith(b'RIFF'))
        self.assertEqual(sent['url'], 'https://api.breeze.blue/v1/text-to-speech/voc_8rsb3nhb7645?output_format=wav_24000')
        self.assertEqual((sent['method'], sent['headers']['xi-api-key']), ('POST', 'bz-secret'))
        body = sent['body']
        self.assertEqual((body['text'], body['model_id'], body['language_code']), ('你好呀', 'breeze-tts-2', 'zh'))
        self.assertEqual(body['voice_settings'], {'speed': 1.0, 'volume': 1.0})
        self.assertTrue(body['instructions'].startswith('声音清亮')); self.assertIn('笑意', body['instructions'])

    def test_english_has_no_chinese_mood_text_and_ranges_are_clamped(self):
        body = payload_for(target(language='en', instructions='', speed=9, volume=0), 'hello', mood='happy')
        self.assertNotIn('instructions', body)
        self.assertEqual(body['voice_settings'], {'speed': 2.0, 'volume': 0.01})
        self.assertNotIn('language_code', payload_for(target(language=''), 'x'))   # 留空交给模型判断

    def test_voice_id_cannot_inject_path(self):
        client, sent = self.capture()
        client.synthesize(target(voice='../admin?x=1'), '你好', CancellationToken())
        self.assertIn('/text-to-speech/..%2Fadmin%3Fx%3D1?output_format', sent['url'])

    def test_bad_input_never_reaches_network(self):
        client, sent = self.capture()
        with self.assertRaises(ValueError): client.synthesize(target(), '字' * 1001, CancellationToken())
        with self.assertRaises(ValueError): client.synthesize(target(voice=''), '你好', CancellationToken())
        self.assertEqual(sent, {})

    def test_non_wav_success_is_failure(self):
        client, _ = self.capture(result=b'{"detail":"ok"}')
        with self.assertRaisesRegex(RuntimeError, 'Breeze Blue'): client.synthesize(target(), '你好', CancellationToken())

    def test_http_error_is_readable_and_hides_key(self):
        error = HTTPError('https://api.breeze.blue', 401, 'no', {}, io.BytesIO(b'{"detail":{"status":"invalid_api_key"}}'))
        client, _ = self.capture(error=error)
        with self.assertRaises(RuntimeError) as caught: client.synthesize(target(), '你好', CancellationToken())
        self.assertIn('HTTP 401', str(caught.exception)); self.assertNotIn('bz-secret', str(caught.exception))


class LocalServerTests(unittest.TestCase):
    """真走一遍 HTTP：确认头和体按文档发出，返回的 WAV 能通过校验；跳转不跟随。"""

    def serve(self, handler):
        server = http.server.HTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/v1'

    def test_round_trip(self):
        seen = {}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.update(path=self.path, key=self.headers.get('xi-api-key'),
                            body=json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                audio = wav_bytes()
                self.send_response(200); self.send_header('Content-Type', 'audio/wav'); self.send_header('Content-Length', str(len(audio)))
                self.end_headers(); self.wfile.write(audio)
            def log_message(self, *a): pass
        base = self.serve(Handler)
        audio = BreezeSpeechClient().synthesize(target(base_url=base), '你好', CancellationToken())
        self.assertEqual(seen['path'], '/v1/text-to-speech/voc_8rsb3nhb7645?output_format=wav_24000')
        self.assertEqual(seen['key'], 'bz-secret'); self.assertEqual(seen['body']['model_id'], 'breeze-tts-2')
        with wave.open(io.BytesIO(audio)) as w: self.assertEqual((w.getframerate(), w.getnchannels()), (24000, 1))

    def test_redirect_is_not_followed(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(302); self.send_header('Location', 'http://127.0.0.1:9/steal'); self.end_headers()
            def log_message(self, *a): pass
        with self.assertRaisesRegex(RuntimeError, 'HTTP 302'):
            BreezeSpeechClient().synthesize(target(base_url=self.serve(Handler)), '你好', CancellationToken())


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 't.sqlite3'); self.db.initialize()
        self.creds = CredentialStore(self.db, {}, protector=FakeProtector())
        self.router = ModelRouter(self.db, SimpleNamespace(offline=False), credentials=self.creds)
        store = ModelConfigStore(self.db)
        ref = self.creds.save('breeze', 'bz-1')
        from dataclasses import replace
        store.put_provider(replace(PROVIDER_PRESETS['breezeblue'], credential=ref))
        store.put_model(MODEL_PRESETS['breeze-tts-2'])
        store.bind(Binding('tts', 'breeze-tts-2', voice='voc_default', language='zh'))
        store.bind(Binding('tts', 'breeze-tts-2', character_id='deepseek-whale', voice='voc_whale', language='zh', speed=1.1))

    def tearDown(self): self.tmp.cleanup()

    def test_voice_per_character(self):
        whale, other = self.router.tts_target('deepseek-whale'), self.router.tts_target('someone')
        self.assertEqual((whale.voice, whale.speed, whale.api_key), ('voc_whale', 1.1, 'bz-1'))
        self.assertEqual(other.voice, 'voc_default')
        self.assertNotIn('bz-1', whale.cache_id); self.assertNotIn('bz-1', repr(whale))   # Key 不进缓存键和日志

    def test_cache_id_changes_with_key_and_missing_key_disables(self):
        before = self.router.tts_target('deepseek-whale').cache_id
        self.creds.save('breeze', 'bz-2')
        self.assertNotEqual(self.router.tts_target('deepseek-whale').cache_id, before)
        self.creds.delete('breeze')
        self.assertIsNone(self.router.tts_target('deepseek-whale'))

    def test_speech_client_requires_configuration(self):
        speech = SpeechClient(SimpleNamespace(offline=False), tts_source=lambda: None)
        with self.assertRaisesRegex(ValueError, 'Breeze Blue'):
            speech.synthesize('你好', '', CancellationToken(), provider='breeze')

    def test_speech_client_uses_current_target(self):
        calls = []
        speech = SpeechClient(SimpleNamespace(offline=False), tts_source=lambda: self.router.tts_target('deepseek-whale'))
        speech.breeze = SimpleNamespace(synthesize=lambda t, text, c, mood, style: calls.append((t.voice, text, mood)) or b'RIFF')
        speech.synthesize('你好', 'ignored', CancellationToken(), provider='breeze', mood='shy')
        self.assertEqual(calls, [('voc_whale', '你好', 'shy')])

    def test_queued_sentence_keeps_its_own_target(self):
        # Codex 审计 R4：排队中的句子用入队时的音色和账户，改设置只影响新句子；缓存键跟着入队时的目标
        from collections import deque
        from aicompanion.speech.output import SpeechOutput
        current = {'cid': 'deepseek-whale'}
        speech = SpeechClient(SimpleNamespace(offline=False), tts_source=lambda: self.router.tts_target(current['cid']))
        used = []
        speech.breeze = SimpleNamespace(synthesize=lambda t, text, c, mood, style: used.append((t.voice, t.base_url)) or wav_bytes())
        # 容器里没有音频设备，不创建真正的播放器：只用入队逻辑
        output = SimpleNamespace(client=speech, provider='breeze', voice='', mood='calm', style='', language='zh', role_style='',
                                 enabled=True, failed_generation=None, queue=deque(), _next=lambda: None, _prefetch=lambda: None)
        output.enqueue = lambda text, gid: SpeechOutput.enqueue(output, text, gid)
        output.enqueue('第一句', 'g')
        current['cid'] = 'someone'                                               # 排队期间换成另一个音色
        output.enqueue('第二句', 'g')
        first, second = list(output.queue)
        self.assertEqual((first.target.voice, second.target.voice), ('voc_whale', 'voc_default'))
        self.assertEqual(first.voice, first.target.cache_id); self.assertNotEqual(first.voice, second.voice)
        for job in (first, second):
            speech.synthesize(job.text, job.voice, CancellationToken(), provider='breeze', target=job.target)
        self.assertEqual([v for v, _ in used], ['voc_whale', 'voc_default'])


if __name__ == '__main__':
    unittest.main()
