"""千问原生语音接口：专属音色设计、情感合成和有界音频下载。"""
import base64
import io
import json
import re
import wave
from urllib.error import HTTPError, URLError
from http.client import HTTPException
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


DEFAULT_STYLE = '用自然的中文面对面交谈，声音轻盈温柔，有细微呼吸与停顿，不要播音腔、机械逐字朗读或夸张撒娇。'
MOOD_STYLES = {
    'calm': '语气平静亲近，像认真倾听后轻声回应，语速适中。',
    'happy': '带一点笑意和轻快的上扬语调，自然开心，不要大声。',
    'shy': '略带害羞，声音稍轻，停顿柔和，保持清楚自然的发音。',
    'sad': '温柔低缓，放慢一点，带克制的失落与关心，不要哭腔。',
    'angry': '轻微不满，语气稍认真，表达在意但不要吼叫。',
}
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def trusted_audio_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if parts.scheme not in ('http', 'https') or parts.username or parts.password or parts.port not in (None, 443) or not host.endswith(('.aliyuncs.com', '.qianwenaiapi.com')):
            raise ValueError()
        return parts._replace(scheme='https').geturl()
    except (ValueError, TypeError, AttributeError) as error:
        raise RuntimeError('千问返回了不受信任的音频下载地址。') from error


class SpeechRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # API 请求绝不带密钥跟随重定向；音频每次跳转都重新检查目标域名。
        if request.get_header('Authorization'):
            raise RuntimeError('千问 API 端点发生重定向，请核对 QWEN_BASE_URL。')
        return super().redirect_request(request, fp, code, message, headers, trusted_audio_url(new_url))


def voice_instruction(mood='calm', style='', language='zh'):
    instruction = (style.strip() or DEFAULT_STYLE) + MOOD_STYLES.get(mood, MOOD_STYLES['calm'])
    if language == 'ja':
        # 音色仍沿用现有原创声线；改变语种不等于复刻原作声优。
        instruction = instruction.replace('中文', '日语')[:900] + '只用自然日语朗读输入台词，发音清晰，保留柔和呼吸和停顿。'
    return instruction[:1000]


def validate_wav(raw, source='千问'):
    # 播放与口型共用单声道 PCM；拒绝错误 JSON、空音频和不受支持的编码。
    try:
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getnframes() < 1:
                raise ValueError()
            if not 8000 <= wav.getframerate() <= 48000:
                raise ValueError()
            pcm = wav.readframes(wav.getnframes())
            if len(pcm) != wav.getnframes() * 2:
                # 千问 HTTP 合成使用 FFmpeg 的未回填流式 WAV 长度；仅修复已知占位头。
                # 普通长度不匹配仍视为截断，不能用“重写文件头”掩盖网络数据损坏。
                riff_size = int.from_bytes(raw[4:8], 'little')
                if riff_size not in (0x7fffffbf, 0x7fffffff, 0xffffffff) or wav.getnframes()*2 < 0x7fff0000 or not pcm or len(pcm)%2:
                    raise ValueError()
                normalized = io.BytesIO()
                with wave.open(normalized, 'wb') as fixed:
                    fixed.setnchannels(1); fixed.setsampwidth(2); fixed.setframerate(wav.getframerate())
                    fixed.writeframes(pcm)
                raw = normalized.getvalue()
    except (wave.Error, EOFError, ValueError) as error:
        raise RuntimeError(f'{source}未返回完整的单声道 16-bit WAV 音频。') from error
    return raw


class QwenSpeechClient:
    def __init__(self, settings):
        self.settings = settings
        self.opener = build_opener(SpeechRedirectHandler())

    def _read(self, request, cancellation, timeout=20):
        from aicompanion.net import ResponseTooLarge, fetch
        try:
            return fetch(request, cancellation, timeout, MAX_AUDIO_BYTES, opener=self.opener)
        except ResponseTooLarge as error:
            raise RuntimeError('千问音频超过大小限制，请缩短回复。') from error
        except HTTPError as error:
            # 不输出请求头、签名下载地址或完整响应，避免诊断日志泄露凭据。
            try:
                code = json.loads(error.read(4096)).get('code', '')
            except (ValueError, AttributeError):
                code = ''
            code = re.sub(r'[^a-zA-Z0-9_.-]', '', str(code))[:64]
            raise RuntimeError(f'千问语音请求失败（HTTP {error.code}，{code or "请检查权限与额度"}）。') from error
        except (URLError, OSError, HTTPException) as error:
            cancellation.check()
            raise RuntimeError('千问语音连接中断或超时，请稍后重试。') from error

    def _post(self, path, payload, cancellation, timeout=20):
        if not self.settings.qwen_api_key:
            raise ValueError('请配置独立的 QWEN_API_KEY，千问语音不能使用 DeepSeek 密钥。')
        request = Request(self.settings.qwen_base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + self.settings.qwen_api_key, 'Content-Type': 'application/json'})
        try:
            result = json.loads(self._read(request, cancellation, timeout))
            if not isinstance(result, dict) or not isinstance(result.get('output'), dict):
                raise ValueError()
            return result['output']
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError('千问语音响应格式无效。') from error

    def _audio(self, output, cancellation):
        if not isinstance(output, dict):
            raise RuntimeError('千问响应缺少音频数据。')
        data = output.get('data')
        if data:
            try:
                raw = base64.b64decode(data, validate=True)
            except (ValueError, TypeError) as error:
                raise RuntimeError('千问音频编码无效。') from error
        else:
            # 音频下载不携带 API key，仅允许官方对象存储和服务域名。
            # 官方部分响应仍标注 HTTP，访问时升级为 HTTPS，避免台词音频明文传输。
            raw = self._read(Request(trusted_audio_url(output.get('url', ''))), cancellation)
        if len(raw) > MAX_AUDIO_BYTES:
            raise RuntimeError('千问音频超过大小限制。')
        return validate_wav(raw)

    def transcribe(self, raw, sample_rate, cancellation):
        cancellation.check()
        raw = validate_wav(raw)
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            # 接口参数必须与录音头一致，直接调用此客户端也不能绕过时长限制。
            if wav.getframerate() != sample_rate or wav.getnframes() > sample_rate * 30:
                raise ValueError('识别采样率必须与 WAV 一致，录音不能超过 30 秒。')
        # 用内存 Data URI 上传录音，不创建可公开访问的录音链接或磁盘临时文件。
        output = self._post('/services/aigc/multimodal-generation/generation', {
            'model': self.settings.qwen_asr_model,
            'input': {'messages': [{'role': 'user', 'content': [{
                'type': 'input_audio', 'input_audio': {'data': 'data:audio/wav;base64,' + base64.b64encode(raw).decode('ascii')},
            }]}]},
            'parameters': {'format': 'wav', 'sample_rate': str(sample_rate)},
        }, cancellation)
        cancellation.check()
        # 非流式结果可能只包含完整 text，或含最终 sentence；不接受未结束的中间句。
        sentence = output.get('sentence')
        if isinstance(sentence, dict) and sentence.get('sentence_end') is False:
            raise RuntimeError('千问尚未完成本段识别，请重新说一次。')
        text = output.get('text')
        if text is None and isinstance(sentence, dict) and sentence.get('sentence_end') is True:
            text = sentence.get('text')
        if not isinstance(text, str) or len(text) > 4000:
            raise RuntimeError('千问语音识别响应格式无效。')
        return text.strip()

    def synthesize(self, text, voice, cancellation, mood='calm', style='', language='zh'):
        if not text.strip() or len(text) > 600:
            raise ValueError('千问单次合成需要 1 到 600 字符，请按句子发送。')
        output = self._post('/services/audio/tts/SpeechSynthesizer', {
            'model': self.settings.qwen_tts_model,
            'input': {'text': text, 'voice': voice or self.settings.qwen_tts_voice,
                      'format': 'wav', 'sample_rate': 24000,
                      'instruction': voice_instruction(mood, style, language)},
        }, cancellation)
        return self._audio(output.get('audio'), cancellation)

    def design_voice(self, prompt, preview_text, cancellation):
        if not 1 <= len(prompt) <= 500 or not 15 <= len(preview_text) <= 200:
            raise ValueError('声线描述最多 500 字符，试听台词需为 15 到 200 字符。')
        output = self._post('/services/audio/tts/customization', {
            'model': 'voice-enrollment',
            'input': {'action': 'create_voice', 'target_model': self.settings.qwen_tts_model,
                      'voice_prompt': prompt, 'preview_text': preview_text,
                      'prefix': 'companion', 'language_hints': ['zh']},
            'parameters': {'sample_rate': 24000, 'response_format': 'wav'},
        }, cancellation, timeout=60)
        voice = output.get('voice_id')
        if not isinstance(voice, str) or not voice:
            raise RuntimeError('千问声音设计未返回音色 ID。')
        return voice, self._audio(output.get('preview_audio'), cancellation)

    def clone_voice(self, audio_url, cancellation):
        """从公开 HTTPS 样本登记实验音色；此接口成功不代表获得素材使用授权。"""
        parts = urlsplit(audio_url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise ValueError('声音样本必须是无需登录的 HTTPS 音频地址。')
        output = self._post('/services/audio/tts/customization', {
            'model': 'voice-enrollment',
            'input': {'action': 'create_voice', 'target_model': self.settings.qwen_tts_model,
                      'prefix': 'emiliajp', 'url': audio_url, 'language_hints': ['ja'],
                      'max_prompt_audio_length': 10.0, 'enable_preprocess': False},
        }, cancellation, timeout=60)
        voice = output.get('voice_id')
        if not isinstance(voice, str) or not voice:
            raise RuntimeError('声音复刻未返回可用音色 ID。')
        return voice
