"""鲸鲸的默认声线（负责人 2026-11-11：网上 AI 视频那种有感情的少女声线）：MiniMax 语音客户端、默认声线路由、心情 → 情绪。
不联网：请求由假的 fetch 接住。"""
import binascii
import io
import json
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.domain.voice_profile import WHALE
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.models import MODEL_PRESETS, PROVIDER_PRESETS, Binding
from aicompanion.providers.router import ModelRouter, TtsTarget
from aicompanion.providers.store import ModelConfigStore
from aicompanion.speech.minimax_client import MiniMaxSpeechClient, payload_for, synthesize_target
from aicompanion.storage.database import Database
from test_breeze_tts import FakeProtector


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\x01\x00'*2400)
    return buffer.getvalue()


def target(**kw):
    base = dict(provider_kind='minimax', base_url='https://api.minimax.io/v1', api_key='mm-key', model_id='speech-2.8-hd',
                voice='Chinese (Mandarin)_Soft_Girl', language='zh', speed=1.0, volume=1.0, instructions='', cache_id='c', label='l')
    base.update(kw); return TtsTarget(**base)


class ClientTests(unittest.TestCase):
    def test_payload_lets_model_pick_emotion_unless_mood_is_clear(self):
        calm = payload_for(target(), '你回来啦')
        self.assertNotIn('emotion', calm['voice_setting'])                                  # 平静：模型按台词自动选情绪
        self.assertEqual(payload_for(target(), '哼', 'angry')['voice_setting']['emotion'], 'angry')
        self.assertEqual(payload_for(target(), '嘿嘿', 'happy')['voice_setting']['emotion'], 'happy')
        self.assertNotIn('emotion', payload_for(target(), '唔', 'shy')['voice_setting'])
        self.assertEqual(calm['audio_setting'], {'sample_rate': 24000, 'format': 'wav', 'channel': 1})
        self.assertEqual((calm['output_format'], calm['language_boost'], calm['model']), ('hex', 'Chinese', 'speech-2.8-hd'))

    def test_synthesize_decodes_hex_wav_and_hides_details_on_error(self):
        seen = []
        def ok(request, token, timeout, limit):
            seen.append(request)
            return json.dumps({'data': {'audio': binascii.hexlify(wav_bytes()).decode(), 'status': 2},
                               'base_resp': {'status_code': 0, 'status_msg': 'success'}}).encode()
        raw = MiniMaxSpeechClient(ok).synthesize(target(), '你回来啦', CancellationToken(), 'happy')
        self.assertEqual(raw[:4], b'RIFF')
        self.assertEqual(seen[0].full_url, 'https://api.minimax.io/v1/t2a_v2')
        self.assertEqual(seen[0].get_header('Authorization'), 'Bearer mm-key')
        def bad(request, token, timeout, limit):
            return json.dumps({'base_resp': {'status_code': 1008, 'status_msg': 'insufficient balance mm-key'}}).encode()
        with self.assertRaises(RuntimeError) as caught:
            MiniMaxSpeechClient(bad).synthesize(target(), '你好', CancellationToken())
        self.assertIn('余额不足', str(caught.exception)); self.assertNotIn('mm-key', str(caught.exception))
        def junk(request, token, timeout, limit):
            return json.dumps({'data': {'audio': 'zz'}, 'base_resp': {'status_code': 0}}).encode()
        with self.assertRaises(RuntimeError): MiniMaxSpeechClient(junk).synthesize(target(), '你好', CancellationToken())

    def test_speech_client_dispatches_by_provider_kind(self):
        from aicompanion.speech.client import SpeechClient
        settings = SimpleNamespace(asr_provider='zhipu', qwen_api_key='', speech_api_key='', speech_base_url='', tts_model='')
        client = SpeechClient(settings)
        calls = []
        client.minimax = SimpleNamespace(synthesize=lambda t, text, token, mood, style: calls.append(('minimax', t.voice, mood)) or b'w')
        client.breeze = SimpleNamespace(synthesize=lambda t, text, token, mood, style: calls.append(('breeze', t.voice, mood)) or b'w')
        client.synthesize('你好', '', CancellationToken(), provider='breeze', mood='sad', target=target())
        client.synthesize('你好', '', CancellationToken(), provider='breeze', target=target(provider_kind='breezeblue', voice='voc_1'))
        self.assertEqual(calls, [('minimax', 'Chinese (Mandarin)_Soft_Girl', 'sad'), ('breeze', 'voc_1', 'calm')])
        self.assertEqual(synthesize_target.__name__, 'synthesize_target')


class DefaultVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'t.sqlite3'); self.db.initialize()
        self.creds = CredentialStore(self.db, {}, protector=FakeProtector())
        self.router = ModelRouter(self.db, SimpleNamespace(offline=False), credentials=self.creds)
        self.store = ModelConfigStore(self.db)

    def tearDown(self): self.tmp.cleanup()

    def add_minimax(self):
        ref = self.creds.save('minimax-global', 'mm-1')
        self.store.put_provider(replace(PROVIDER_PRESETS['minimax-global'], credential=ref))

    def test_whale_uses_minimax_voice_without_extra_setup(self):
        self.assertIsNone(self.router.tts_target('deepseek-whale'))                         # 没有 MiniMax 账户：没有默认声线
        self.add_minimax()
        voice = self.router.tts_target('deepseek-whale')
        self.assertEqual((voice.provider_kind, voice.model_id, voice.voice), ('minimax', 'speech-2.8-hd', WHALE.minimax_voice))
        self.assertEqual(voice.api_key, 'mm-1'); self.assertIn('默认声线', voice.label); self.assertNotIn('mm-1', voice.cache_id)
        self.assertIsNone(self.router.tts_target('someone-else'))                           # 没有声线设定的角色不套用
        self.db.set_setting('voice_choice:deepseek-whale', 'Arrogant_Miss')
        self.assertEqual(self.router.tts_target('deepseek-whale').voice, 'Arrogant_Miss')   # 候选里换一个
        self.assertNotEqual(self.router.tts_target('deepseek-whale').cache_id, voice.cache_id)
        self.db.set_setting('voice_choice:deepseek-whale', 'not-a-candidate')
        self.assertEqual(self.router.tts_target('deepseek-whale').voice, WHALE.minimax_voice)

    def test_explicit_model_page_voice_wins(self):
        self.add_minimax()
        self.store.put_model(MODEL_PRESETS['minimax-speech-2.8-turbo'])
        self.store.bind(Binding('tts', 'minimax-speech-2.8-turbo', character_id='deepseek-whale', voice='Chinese (Mandarin)_Crisp_Girl', language='zh'))
        voice = self.router.tts_target('deepseek-whale')
        self.assertEqual((voice.model_id, voice.voice), ('speech-2.8-turbo', 'Chinese (Mandarin)_Crisp_Girl'))
        self.assertNotIn('默认声线', voice.label)

    def test_offline_has_no_default(self):
        self.add_minimax()
        offline = ModelRouter(self.db, SimpleNamespace(offline=True), credentials=self.creds)
        self.assertIsNone(offline.tts_target('deepseek-whale'))


if __name__ == '__main__':
    unittest.main()
