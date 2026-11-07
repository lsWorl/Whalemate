import base64
from collections import deque
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request
import wave

from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.speech.qwen_client import QwenSpeechClient, validate_wav, voice_instruction, SpeechRedirectHandler
from aicompanion.speech.output import SpeechOutput, SpeechSegment, spoken_text, SentenceBuffer


def wav_bytes(channels=1):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(channels); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(bytes(4800*channels))
    return buffer.getvalue()


class QwenTests(unittest.TestCase):
    def setUp(self):
        self.settings=SimpleNamespace(qwen_api_key='dummy-qwen',qwen_base_url='https://maas.qianwenaiapi.com/api/v1',
            qwen_tts_model='qwen-audio-3.0-tts-flash',qwen_tts_voice='custom-voice')
        self.client=QwenSpeechClient(self.settings)
        self.token=CancellationToken(); self.wav=wav_bytes()

    def test_style_and_voice_are_sent_with_native_protocol(self):
        output={'audio':{'data':base64.b64encode(self.wav).decode()}}
        with patch.object(self.client,'_post',return_value=output) as post:
            raw=self.client.synthesize('你好。','custom-voice',self.token,'shy')
        self.assertEqual(raw,self.wav)
        payload=post.call_args.args[1]
        self.assertEqual(payload['input']['voice'],'custom-voice')
        self.assertIn('害羞',payload['input']['instruction'])
        self.assertEqual(payload['input']['sample_rate'],24000)

    def test_qwen_credential_is_only_in_request_header(self):
        with patch.object(self.client,'_read',return_value=b'{"output":{}}') as read:
            self.client._post('/test',{'model':'test'},self.token)
        request=read.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer dummy-qwen')
        self.assertNotIn(b'dummy-qwen',request.data)

    def test_media_download_uses_https_without_credentials(self):
        with patch.object(self.client,'_read',return_value=self.wav) as read:
            self.client._audio({'url':'http://example.oss-cn-beijing.aliyuncs.com/a.wav?signature=test'},self.token)
        request=read.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://'))
        self.assertIsNone(request.get_header('Authorization'))

    def test_untrusted_media_urls_are_rejected(self):
        for url in ['https://evil.example/a.wav','http://127.0.0.1/a.wav','file:///secret.wav',
                    'https://user:password@example.aliyuncs.com/a.wav','https://example.aliyuncs.com:8080/a.wav']:
            with self.subTest(url=url),patch.object(self.client,'_read') as read,self.assertRaises(RuntimeError):
                self.client._audio({'url':url},self.token)
            read.assert_not_called()

    def test_wave_invalid_stereo_and_truncated_are_rejected(self):
        for raw in [b'{}',wav_bytes(2),self.wav[:-4]]:
            with self.subTest(size=len(raw)),self.assertRaises(RuntimeError): validate_wav(raw)

    def test_known_streaming_wave_header_is_normalized(self):
        raw=bytearray(self.wav)
        raw[4:8]=(0x7fffffbf).to_bytes(4,'little')
        raw[40:44]=(0x7fffff9b).to_bytes(4,'little')
        normalized=validate_wav(bytes(raw))
        self.assertEqual(normalized,self.wav)

    def test_cancelled_request_never_connects(self):
        self.token.cancel()
        with patch.object(self.client.opener,'open') as connect,self.assertRaises(ReplyCancelled):
            self.client._read(Mock(),self.token)
        connect.assert_not_called()

    def test_api_credential_cannot_follow_redirect(self):
        request=Request('https://maas.qianwenaiapi.com/api/v1',headers={'Authorization':'Bearer dummy'})
        with self.assertRaises(RuntimeError):
            SpeechRedirectHandler().redirect_request(request,None,302,'redirect',{},'https://evil.example/')

    def test_download_redirect_cannot_escape_official_hosts(self):
        request=Request('https://bucket.aliyuncs.com/a.wav')
        with self.assertRaises(RuntimeError):
            SpeechRedirectHandler().redirect_request(request,None,302,'redirect',{},'http://127.0.0.1/')

    def test_design_voice_parses_preview_and_id(self):
        output={'voice_id':'designed-voice','preview_audio':{'data':base64.b64encode(self.wav).decode()}}
        with patch.object(self.client,'_post',return_value=output) as post:
            voice,raw=self.client.design_voice('温柔成年女声','这是一段超过十五个字符的声音设计测试台词。',self.token)
        self.assertEqual((voice,raw),('designed-voice',self.wav))
        self.assertEqual(post.call_args.args[1]['input']['target_model'],self.settings.qwen_tts_model)

    def test_text_limit_rejects_before_network(self):
        with patch.object(self.client,'_post') as post,self.assertRaises(ValueError):
            self.client.synthesize('啊'*601,'voice',self.token)
        post.assert_not_called()

    def test_custom_style_and_unknown_mood(self):
        self.assertIn('用轻声说话',voice_instruction('unknown','用轻声说话。'))
        self.assertIn('平静',voice_instruction('unknown'))

    def test_clone_voice_uses_japanese_reference_and_current_target(self):
        with patch.object(self.client,'_post',return_value={'voice_id':'experiment-voice'}) as post:
            voice=self.client.clone_voice('https://example.com/sample.mp3',self.token)
        self.assertEqual(voice,'experiment-voice')
        payload=post.call_args.args[1]
        self.assertEqual(payload['input']['language_hints'],['ja'])
        self.assertEqual(payload['input']['target_model'],self.settings.qwen_tts_model)
        self.assertEqual(payload['input']['max_prompt_audio_length'],10.0)

    def test_clone_voice_rejects_insecure_sample_url(self):
        with patch.object(self.client,'_post') as post,self.assertRaises(ValueError):
            self.client.clone_voice('http://example.com/sample.mp3',self.token)
        post.assert_not_called()

    def test_clone_voice_without_id_is_not_success(self):
        with patch.object(self.client,'_post',return_value={}),self.assertRaises(RuntimeError):
            self.client.clone_voice('https://example.com/sample.mp3',self.token)


class FakeSignal:
    def __init__(self): self.slots=[]
    def connect(self,slot): self.slots.append(slot)
    def emit(self,*args):
        for slot in self.slots: slot(*args)


class FakeWorker:
    def __init__(self,function,parent):
        self.function=function; self.succeeded=FakeSignal(); self.failed=FakeSignal(); self.finished=FakeSignal()
    def start(self): pass
    def deleteLater(self): pass


class SpeechQueueTests(unittest.TestCase):
    def test_prefetch_is_bounded_and_captures_sentence_settings(self):
        jobs=[SpeechSegment(str(n),'generation','qwen','voice-a','shy','style-a','ja','role-a') for n in range(4)]
        client=Mock(); client.synthesize.return_value=wav_bytes()
        output=SimpleNamespace(current_job=jobs[0],queue=deque(jobs[1:]),workers=set(),epoch=2,client=client,
            busy=True,_play=Mock(),_error=Mock(),_dispose=Mock(),_cached=lambda job:None)
        output._ready=lambda *args: SpeechOutput._ready(output,*args)
        with patch('aicompanion.speech.output.TaskWorker',FakeWorker):
            SpeechOutput._prefetch(output); SpeechOutput._prefetch(output)
        self.assertEqual(len(output.workers),2)
        self.assertTrue(jobs[0].requested and jobs[1].requested)
        self.assertFalse(jobs[2].requested)
        for worker in output.workers: worker.function(CancellationToken())
        self.assertEqual(client.synthesize.call_args.kwargs,{'provider':'qwen','mood':'shy','style':'style-a','language':'ja','role_style':'role-a'})
        output.epoch+=1
        for worker in output.workers:
            worker.succeeded.emit(wav_bytes()); worker.failed.emit('late error')
        output._play.assert_not_called(); output._error.assert_not_called()
        self.assertIsNone(jobs[0].audio)

    def test_spoken_text_removes_markdown_and_link_address(self):
        self.assertEqual(spoken_text('**你好**，[今天](https://example.com)。'),'你好，今天。')

    def test_ellipsis_and_semicolon_flush_sentences(self):
        self.assertEqual(SentenceBuffer().feed('嗯……我在听；你慢慢说'),['嗯……','我在听；'])


if __name__=='__main__': unittest.main()
