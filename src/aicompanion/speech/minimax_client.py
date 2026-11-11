"""MiniMax 语音合成（计划书 v4 功能八：鲸鲸的默认声线）。

接口（官方文档 platform.minimax.io/docs/api-reference/speech-t2a-http，2026-11-11 核对）：
    POST {base}/t2a_v2          base 与聊天同一个 MiniMax 账户地址（国际站 https://api.minimax.io/v1）
    头：Authorization: Bearer <Key>
    体：{"model", "text", "stream": false, "voice_setting": {"voice_id", "speed", "vol", "emotion"?},
         "audio_setting": {"sample_rate": 24000, "format": "wav", "channel": 1}, "output_format": "hex", "language_boost"}
    返回：{"data": {"audio": "<hex>", "status": 2}, "base_resp": {"status_code": 0, ...}}
    不传 emotion 时模型按台词自动选最自然的情绪；2.8 模型支持 (laughs) 这类语气标签。

请求不跟随跳转（Key 不外泄），可随时取消；返回必须是完整 WAV 才交给播放器。
"""
from __future__ import annotations

import binascii
import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request

from aicompanion.domain.voice_profile import EMOTIONS
from aicompanion.net import ResponseTooLarge, fetch
from aicompanion.speech.qwen_client import validate_wav

MAX_TEXT = 3000             # 官方上限 1 万字，超过 3000 建议流式；一句朗读远到不了
MAX_RESPONSE = 40 * 1024 * 1024
ERRORS = {1002: '请求太频繁', 1004: 'Key 无效', 1008: '余额不足', 1039: '请求太频繁', 1042: '文字里无效字符太多',
          2013: '参数不被接受（模型或音色 ID 是否正确）', 2049: 'Key 无效'}


def payload_for(target, text: str, mood: str = 'calm') -> dict:
    voice = {'voice_id': target.voice, 'speed': max(0.5, min(2.0, float(target.speed or 1.0))),
             'vol': max(0.1, min(10.0, float(target.volume or 1.0)))}
    emotion = EMOTIONS.get(mood)
    if emotion: voice['emotion'] = emotion
    return {'model': target.model_id, 'text': text, 'stream': False, 'voice_setting': voice,
            'audio_setting': {'sample_rate': 24000, 'format': 'wav', 'channel': 1},
            'output_format': 'hex', 'language_boost': 'Japanese' if target.language == 'ja' else 'Chinese'}


class MiniMaxSpeechClient:
    def __init__(self, open_fetch=fetch):
        self.fetch = open_fetch   # 测试注入

    def synthesize(self, target, text: str, cancellation, mood: str = 'calm', style: str = '') -> bytes:
        text = str(text or '').strip()
        if not text: raise ValueError('没有要朗读的内容。')
        if len(text) > MAX_TEXT: raise ValueError('这句太长了，MiniMax 单次最多朗读 3000 字。')
        if not target.voice: raise ValueError('还没有选 MiniMax 音色。')
        request = Request(target.base_url.rstrip('/')+'/t2a_v2',
                          data=json.dumps(payload_for(target, text, mood), ensure_ascii=False).encode('utf-8'), method='POST',
                          headers={'Authorization': 'Bearer '+target.api_key, 'Content-Type': 'application/json'})
        try:
            raw = self.fetch(request, cancellation, 30, MAX_RESPONSE)
        except ResponseTooLarge as error:
            raise RuntimeError('MiniMax 音频超过大小限制，请缩短回复。') from error
        except HTTPError as error:
            raise RuntimeError(f'MiniMax 语音请求失败（HTTP {error.code}，请检查 Key 与额度）。') from error
        except (URLError, OSError, HTTPException) as error:
            cancellation.check()
            raise RuntimeError('MiniMax 语音连接中断或超时，请稍后重试。') from error
        try:
            body = json.loads(raw)
        except ValueError as error:
            raise RuntimeError('MiniMax 语音返回格式无效。') from error
        status = (body.get('base_resp') or {}).get('status_code', -1) if isinstance(body, dict) else -1
        if status != 0:
            # 只给错误码和对应说明，不输出响应全文
            raise RuntimeError(f'MiniMax 语音合成失败（{status}，{ERRORS.get(status, "请检查配置与额度")}）。')
        audio = ((body.get('data') or {}).get('audio') or '') if isinstance(body.get('data'), dict) else ''
        try:
            wav = binascii.unhexlify(audio)
        except (binascii.Error, TypeError) as error:
            raise RuntimeError('MiniMax 语音返回的音频无效。') from error
        return validate_wav(wav, 'MiniMax ')


def synthesize_target(target, text, cancellation, mood='calm', style=''):
    """按配置的服务种类选客户端（模型页的“语音合成”可以是 Breeze Blue 也可以是 MiniMax）。"""
    if target.provider_kind == 'minimax':
        return MiniMaxSpeechClient().synthesize(target, text, cancellation, mood, style)
    from aicompanion.speech.breeze_client import BreezeSpeechClient
    return BreezeSpeechClient().synthesize(target, text, cancellation, mood, style)
