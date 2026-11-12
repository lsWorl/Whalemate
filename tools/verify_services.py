"""小样本收费服务验收：不使用私人对话或麦克风录音。"""
import io
import wave
from aicompanion.config import load_settings
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.llm.base import ChatTurn
from aicompanion.speech.client import SpeechClient


def main():
    settings=load_settings(); token=CancellationToken()
    from aicompanion.providers import real_chat_client
    client,_=real_chat_client(settings=settings)
    reply=''.join(client.generate_reply_stream([ChatTurn('user','今天有点累，请用一句温和的话回应。')],'用中文简短回应。',token))
    print('LLM:',reply,flush=True)
    speech=SpeechClient(settings)
    raw=speech.synthesize('你好，今天很高兴见到你。','tongtong',token)
    with wave.open(io.BytesIO(raw),'rb') as wav:
        pcm=wav.readframes(wav.getnframes()); rate=wav.getframerate()
    print('TTS:',len(raw),'bytes,',rate,'Hz',flush=True)
    text=speech.transcribe(pcm,rate,token)
    print('ASR:',text,flush=True)


if __name__=='__main__': main()
