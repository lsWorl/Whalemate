"""Breeze Blue 语音合成（计划书 v4 功能一：音色可在模型配置里按角色设置）。

接口（官方文档 docs.breezeblue.ai，2026-11-11 核对）：
    POST {base}/text-to-speech/{voice_id}?output_format=wav_24000
    头：xi-api-key: <Key>
    体：{"text", "model_id", "language_code"?, "instructions"?, "voice_settings": {"speed", "volume"}}
    返回：24 kHz 单声道 WAV 原始字节。单次最多 1000 字，超过会被拒绝而不是截断。

请求不跟随跳转（Key 不外泄），可随时取消；返回内容必须是完整 WAV 才交给播放器。
"""
from __future__ import annotations

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from aicompanion.net import ResponseTooLarge, fetch
from aicompanion.speech.qwen_client import MOOD_STYLES, validate_wav

MAX_TEXT = 1000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
OUTPUT_FORMAT = 'wav_24000'


def instructions_for(target, mood: str = 'calm', style: str = '') -> str:
    """固定语气说明 + 当前情绪说明。官方要求说明和正文同一语言；中文正文用中文说明。"""
    parts = [target.instructions.strip(), style.strip()]
    if (target.language or 'zh') == 'zh':
        parts.append(MOOD_STYLES.get(mood, ''))
    return ''.join(p for p in parts if p)[:500]


def _clamp(value, low, high) -> float:
    """官方取值范围：语速 0.5～2.0，音量 0.01～2.0；未设置按 1.0。"""
    return max(low, min(high, float(1.0 if value is None else value)))


def payload_for(target, text: str, mood: str = 'calm', style: str = '') -> dict:
    body = {'text': text, 'model_id': target.model_id,
            'voice_settings': {'speed': _clamp(target.speed, 0.5, 2.0), 'volume': _clamp(target.volume, 0.01, 2.0)}}
    if target.language: body['language_code'] = target.language
    instruction = instructions_for(target, mood, style)
    if instruction: body['instructions'] = instruction
    return body


class BreezeSpeechClient:
    def __init__(self, open_fetch=fetch):
        self.fetch = open_fetch   # 测试注入

    def synthesize(self, target, text: str, cancellation, mood: str = 'calm', style: str = '') -> bytes:
        text = str(text or '').strip()
        if not text: raise ValueError('没有要朗读的内容。')
        if len(text) > MAX_TEXT: raise ValueError('这句太长了，Breeze Blue 单次最多朗读 1000 字。')
        if not target.voice: raise ValueError('还没有在“模型与 Key”页填写 Breeze Blue 的音色 ID。')
        url = f"{target.base_url.rstrip('/')}/text-to-speech/{quote(target.voice, safe='')}?output_format={OUTPUT_FORMAT}"
        request = Request(url, data=json.dumps(payload_for(target, text, mood, style), ensure_ascii=False).encode('utf-8'),
                          method='POST', headers={'xi-api-key': target.api_key, 'Content-Type': 'application/json',
                                                  'Accept': 'audio/wav'})
        try:
            raw = self.fetch(request, cancellation, 30, MAX_AUDIO_BYTES)
        except ResponseTooLarge as error:
            raise RuntimeError('Breeze Blue 音频超过大小限制，请缩短回复。') from error
        except HTTPError as error:
            # 只取错误码字段，不输出请求头和完整响应
            try:
                detail = json.loads(error.read(4096) or b'{}')
                code = (detail.get('detail') or {}).get('status', '') if isinstance(detail.get('detail'), dict) else detail.get('code', '')
            except (ValueError, AttributeError):
                code = ''
            code = re.sub(r'[^a-zA-Z0-9_.-]', '', str(code))[:64]
            hint = {401: '请检查 Breeze Blue 的 Key', 403: '账户没有这个音色或模型的权限', 404: '音色 ID 不存在',
                    422: '参数不被接受（语言和模型是否匹配）', 429: '请求太频繁或额度不足'}.get(error.code, code or '请检查配置与额度')
            raise RuntimeError(f'Breeze Blue 语音请求失败（HTTP {error.code}，{hint}）。') from error
        except (URLError, OSError, HTTPException) as error:
            cancellation.check()
            raise RuntimeError('Breeze Blue 语音连接中断或超时，请稍后重试。') from error
        return validate_wav(raw, 'Breeze Blue ')
