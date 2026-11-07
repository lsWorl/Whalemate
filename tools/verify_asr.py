"""使用已生成的中日文测试音频验收识别，不录麦克风或上传私人聊天。"""
import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic
import wave

from aicompanion.config import load_settings
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.speech.client import SpeechClient

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Verify Qwen ASR using synthetic audio.')
    parser.add_argument('--refresh', action='store_true', help='Send new paid recognition requests.')
    args = parser.parse_args()
    settings = load_settings()
    paths = [ROOT/'docs/audio/anime-calm.wav', ROOT/'docs/audio/anime-ja.wav']
    fingerprints = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report_path = ROOT/'docs/asr-smoke.json'
    if report_path.exists() and not args.refresh:
        cached = json.loads(report_path.read_text(encoding='utf-8'))
        if cached.get('model')==settings.qwen_asr_model and cached.get('fingerprints')==fingerprints and cached.get('passed'):
            print('ASR verified from cached report; no API requests.')
            return 0
    client = SpeechClient(settings)
    client.asr_provider = 'qwen'
    samples = []
    for path in paths:
        with wave.open(str(path), 'rb') as source:
            assert source.getnchannels()==1 and source.getsampwidth()==2
            rate = source.getframerate(); pcm = source.readframes(source.getnframes())
        start = monotonic()
        text = client.transcribe(pcm, rate, CancellationToken())
        # 非空只证明接口有效，识别准确度需要对照原台词人工核查。
        samples.append({'fixture': path.name, 'text': text, 'request_seconds': round(monotonic()-start, 2),
            'audio_seconds': round(len(pcm)/2/rate, 2)})
    report = {'provider': 'qwen', 'model': settings.qwen_asr_model, 'fingerprints': fingerprints,
        'samples': samples, 'passed': all(sample['text'] for sample in samples)}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
