from __future__ import annotations

import io
import json
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import wave

from aicompanion.domain.cancellation import CancellationToken


class SpeechClient:
    """智谱 ASR/TTS 适配器；凭据只用于请求头，录音不写磁盘。"""

    def __init__(self,settings):
        self.settings = settings

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
        output = io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(sample_rate)
            wav.writeframes(pcm)
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

    def synthesize(self,text,voice,cancellation):
        payload = {'model':self.settings.tts_model,'input':text[:1024],'voice':voice or 'tongtong','response_format':'wav'}
        raw = self._request('/audio/speech',json.dumps(payload).encode(),'application/json',cancellation)
        # 必须是真正 WAV；错误 JSON 不交给播放器。
        with wave.open(io.BytesIO(raw),'rb') as wav:
            if wav.getsampwidth()!=2 or wav.getnchannels()!=1:
                raise RuntimeError('语音格式不是单声道 16-bit PCM WAV。')
        return raw

