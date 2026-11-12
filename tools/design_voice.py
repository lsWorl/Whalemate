"""创建原创动漫风声线；只上传描述和测试台词，不使用声优录音。"""
from pathlib import Path
import json
import argparse
from dotenv import set_key
from aicompanion.config import load_settings
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.speech.qwen_client import QwenSpeechClient

ROOT = Path(__file__).resolve().parents[1]
PROMPT = '二十岁左右的年轻成年女性，日系治愈动漫女主风格的原创声线。中高音区，明亮清透，轻盈柔软，少量自然气息感。中文普通话清晰自然，像亲近的人轻声聊天，略带含蓄和害羞，情绪细腻，有自然停顿和语调变化。声音有温度，不是客服、播音员或机械电子音；不要幼儿音、尖锐假声、夸张撒娇，不模仿任何真人声优。'
PREVIEW = '你回来啦。今天是不是有一点累？没关系，我在这里。你可以慢慢说，我会认真听着的。'


def main():
    settings = load_settings()
    parser=argparse.ArgumentParser(description='Design an original anime-inspired voice.')
    parser.add_argument('--regenerate',action='store_true')
    args=parser.parse_args()
    if settings.qwen_tts_voice.startswith(settings.qwen_tts_model+'-vd-') and not args.regenerate:
        print('A designed voice is already configured. Use --regenerate to create a new voice.')
        return
    voice, audio = QwenSpeechClient(settings).design_voice(PROMPT, PREVIEW, CancellationToken())
    output = ROOT/'docs/audio'
    output.mkdir(parents=True, exist_ok=True)
    (output/'anime-preview.wav').write_bytes(audio)
    (output/'voice-design.json').write_text(json.dumps({'model': settings.qwen_tts_model,
        'voice': voice, 'prompt': PROMPT, 'preview': PREVIEW}, ensure_ascii=False, indent=2), encoding='utf-8')
    # 声线只创建一次，之后所有回复复用音色 ID，避免每次聊天重新收费设计。
    set_key(ROOT/'.env', 'QWEN_TTS_VOICE', voice)
    set_key(ROOT/'.env', 'TTS_PROVIDER', 'qwen')
    print('Voice designed and saved:', output/'anime-preview.wav', flush=True)


if __name__ == '__main__':
    main()
