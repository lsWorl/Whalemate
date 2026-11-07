from __future__ import annotations

import io
import json
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import wave

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.speech.qwen_client import QwenSpeechClient
from aicompanion.speech.translation import JapaneseSpeechTranslator


class SpeechClient:
    """识别和合成各自分流，千问、智谱与聊天服务的凭据不会混用。"""

    def __init__(self,settings):
        self.settings = settings
        self.qwen = QwenSpeechClient(settings)
        self.translator = JapaneseSpeechTranslator(settings)
        self.asr_provider = getattr(settings, 'asr_provider', 'zhipu')

    def ensure_recognition_ready(self):
        if self.asr_provider == 'local':
            from aicompanion.voice import local_asr
            problem = local_asr.status()
            if problem: raise ValueError(problem)
        elif self.asr_provider == 'qwen':
            if not self.settings.qwen_api_key:
                raise ValueError('请配置 QWEN_API_KEY，千问语音识别不能使用聊天密钥。')
        elif self.asr_provider == 'zhipu':
            if not self.settings.speech_api_key:
                raise ValueError('请配置独立 SPEECH_API_KEY，或在设置中选择千问语音识别。')
        else:
            raise ValueError('不支持的语音识别服务，请在设置中重新选择。')

    def _request(self,path,data,content_type,cancellation):
        cancellation.check()
        if not self.settings.speech_api_key:
            raise ValueError('请在 .env 设置 SPEECH_API_KEY 或 LLM_API_KEY。')
        request = Request(self.settings.speech_base_url+path,data=data,headers={
            'Authorization':'Bearer '+self.settings.speech_api_key,'Content-Type':content_type})
        try:
            with urlopen(request,timeout=10) as response:
                result = response.read(25*1024*1024)
            cancellation.check()
            return result
        except HTTPError as error:
            raise RuntimeError(f'语音服务请求失败（HTTP {error.code}），请检查语音权限、额度与配置。') from error
        except (URLError,OSError) as error:
            cancellation.check()
            raise RuntimeError('语音服务连接失败或超时，请检查网络后重试。') from error

    def transcribe(self,pcm,sample_rate,cancellation):
        cancellation.check()
        self.ensure_recognition_ready()
        # 每段最多 30 秒；拒绝不完整 PCM 和不支持的采样率，避免无效录音产生用量。
        if sample_rate not in (8000,16000,24000,32000,48000) or not pcm or len(pcm)%2 or len(pcm)>sample_rate*2*30:
            raise ValueError('录音必须为不超过 30 秒的完整单声道 16-bit PCM。')
        if self.asr_provider == 'local':
            from aicompanion.voice import local_asr
            return local_asr.transcribe(pcm,sample_rate,cancellation)
        output = io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        if self.asr_provider == 'qwen':
            return self.qwen.transcribe(output.getvalue(),sample_rate,cancellation)
        boundary = uuid4().hex
        chunks = []
        for name,value in [('model',self.settings.asr_model),('stream','false')]:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()+output.getvalue()+b'\r\n')
        chunks.append(f'--{boundary}--\r\n'.encode())
        raw = self._request('/audio/transcriptions',b''.join(chunks),'multipart/form-data; boundary='+boundary,cancellation)
        data = json.loads(raw)
        text = data.get('text') if isinstance(data,dict) else None
        if not isinstance(text,str):
            raise RuntimeError('语音识别返回格式无效。')
        return text.strip()

    def synthesize(self,text,voice,cancellation,provider='cloud',mood='calm',style='',language='zh',role_style=''):
        if language not in ('zh','ja'):
            raise ValueError('不支持的朗读语言。')
        if language=='ja':
            if provider!='qwen':
                raise ValueError('日语配音请使用千问语音，避免中文引擎误读日文。')
            # 翻译和合成使用同一取消令牌；打断后不能继续把过期译文送去合成。
            text=self.translator.translate(text,cancellation,role_style)
            cancellation.check()
        if provider == 'qwen':
            return self.qwen.synthesize(text,voice,cancellation,mood,style,language)
        payload = {'model':self.settings.tts_model,'input':text[:1024],'voice':voice or 'tongtong','response_format':'wav'}
        raw = self._request('/audio/speech',json.dumps(payload).encode(),'application/json',cancellation)
        # 必须是真正 WAV；错误 JSON 不交给播放器。
        with wave.open(io.BytesIO(raw),'rb') as wav:
            if wav.getsampwidth()!=2 or wav.getnchannels()!=1:
                raise RuntimeError('语音格式不是单声道 16-bit PCM WAV。')
        return raw

