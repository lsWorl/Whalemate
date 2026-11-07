"""验证已创建声线与语气控制；仅上传合成测试台词，不读取私人聊天。"""
from pathlib import Path
import io
import wave
import argparse
from aicompanion.config import load_settings
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.speech.qwen_client import QwenSpeechClient,validate_wav


def main():
    settings=load_settings()
    parser=argparse.ArgumentParser(description='Verify and save anime voice samples.')
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    client=QwenSpeechClient(settings)
    output=Path(__file__).resolve().parents[1]/'docs/audio'
    output.mkdir(parents=True,exist_ok=True)
    for mood,text in [('calm','今天辛苦了。你可以慢慢说，我会在这里认真听着，不急着给你建议。'),
                      ('shy','能这样和你说说话，我其实有一点开心。嗯，你愿意的话，我想再陪你一会儿。')]:
        target=output/f'anime-{mood}.wav'
        cached=target.exists() and not args.refresh
        raw=validate_wav(target.read_bytes()) if cached else client.synthesize(text,settings.qwen_tts_voice,CancellationToken(),mood)
        target.write_bytes(raw)
        with wave.open(io.BytesIO(raw),'rb') as wav:
            print(mood, 'seconds=',round(wav.getnframes()/wav.getframerate(),2),'sample_rate=',wav.getframerate(),'cached=',cached,flush=True)


if __name__=='__main__': main()
