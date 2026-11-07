"""用合成台词验收中译日和千问合成；默认复用缓存，不上传私人聊天。"""
import argparse
import json
from pathlib import Path
from time import monotonic

from aicompanion.config import load_settings
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.speech.client import SpeechClient
from aicompanion.speech.qwen_client import validate_wav


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Verify Japanese speech with Chinese subtitles.')
    parser.add_argument('--refresh', action='store_true', help='Send new paid translation and speech requests.')
    args = parser.parse_args()
    output = ROOT/'docs/audio'
    output.mkdir(parents=True, exist_ok=True)
    audio_path, report_path = output/'anime-ja.wav', output/'anime-ja.json'
    if audio_path.exists() and report_path.exists() and not args.refresh:
        validate_wav(audio_path.read_bytes())
        print('Japanese audio verified from local cache; no API requests.')
        return
    settings = load_settings()
    client = SpeechClient(settings)
    token = CancellationToken()
    chinese = '你回来啦。今天辛苦了，慢慢说，我在听。'
    start = monotonic()
    japanese = client.translator.translate(chinese, token)
    translation_seconds = monotonic()-start
    # synthesize 复用刚产生的内存翻译，验证完整入口而不重复支付翻译请求。
    start = monotonic()
    raw = client.synthesize(chinese, settings.qwen_tts_voice, token, provider='qwen', language='ja')
    audio_path.write_bytes(validate_wav(raw))
    report = {'chinese': chinese, 'japanese': japanese, 'translation_seconds': round(translation_seconds, 2),
              'synthesis_seconds': round(monotonic()-start, 2), 'voice_type': 'original-designed-not-actor-clone'}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
