"""本地语音识别：sherpa-onnx + SenseVoice（中英日韩粤），离线可用、不产生接口用量。

模型不随仓库分发，由 tools/setup_local_asr.bat 下载到 %LOCALAPPDATA%\\AICompanion\\models\\sense-voice。
识别器第一次用到时才加载（约 1–2 秒、常驻约 250 MB），闲置 10 分钟自动释放。
"""
from __future__ import annotations

from array import array
import os
from pathlib import Path
import re
import threading
import time

IDLE_RELEASE = 10*60
_lock = threading.Lock()
_recognizer = None
_last_used = 0.0


def models_root() -> Path:
    base = os.getenv('LOCALAPPDATA') or str(Path.home()/'.local'/'share')
    return Path(base)/'AICompanion'/'models'


def model_dir() -> Path:
    return Path(os.getenv('LOCAL_ASR_MODEL_DIR') or models_root()/'sense-voice')


def find_model(root: Path | None = None) -> tuple[Path, Path] | None:
    """在模型目录（含解压出的子目录）里找 model(.int8).onnx 和 tokens.txt。"""
    root = Path(root or model_dir())
    if not root.exists(): return None
    for folder in [root, *sorted(p for p in root.iterdir() if p.is_dir())]:
        tokens = folder/'tokens.txt'
        model = next((folder/n for n in ('model.int8.onnx', 'model.onnx') if (folder/n).exists()), None)
        if model and tokens.exists(): return model, tokens
    return None


def package_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec('sherpa_onnx') is not None
    except (ImportError, ValueError):
        return False


def status() -> str:
    """给设置页看的一句话。空串表示可用。"""
    if not package_available(): return '未安装 sherpa-onnx，请运行 tools\\setup_local_asr.bat'
    if not find_model(): return f'没有找到 SenseVoice 模型（{model_dir()}），请运行 tools\\setup_local_asr.bat'
    return ''


TAG = re.compile(r'<\|[^|>]*\|>')


def clean_text(text: str) -> str:
    """去掉 SenseVoice 可能残留的 <|zh|><|NEUTRAL|> 之类标签。"""
    return TAG.sub('', text or '').strip()


def _load():
    global _recognizer
    if _recognizer is None:
        found = find_model()
        if not found: raise RuntimeError(status() or '本地识别模型缺失。')
        import sherpa_onnx
        model, tokens = found
        threads = max(1, min(4, (os.cpu_count() or 2)//2))
        _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model), tokens=str(tokens), num_threads=threads, use_itn=True, language='auto')
    return _recognizer


def _samples(pcm: bytes):
    try:
        import numpy as np
        return np.frombuffer(pcm[:len(pcm)//2*2], dtype=np.int16).astype(np.float32)/32768.0
    except ImportError:
        values = array('h'); values.frombytes(pcm[:len(pcm)//2*2])
        return [v/32768.0 for v in values]


def transcribe(pcm: bytes, sample_rate: int, cancellation=None) -> str:
    global _last_used
    if cancellation: cancellation.check()
    if not pcm: return ''
    with _lock:
        recognizer = _load()
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, _samples(pcm))
        recognizer.decode_stream(stream)
        _last_used = time.monotonic()
        result = stream.result
    if cancellation: cancellation.check()
    return clean_text(getattr(result, 'text', result if isinstance(result, str) else ''))


def loaded() -> bool:
    return _recognizer is not None


def release_if_idle(now: float | None = None, idle: float = IDLE_RELEASE) -> bool:
    global _recognizer
    now = time.monotonic() if now is None else now
    if _recognizer is not None and now-_last_used >= idle and _lock.acquire(blocking=False):
        try: _recognizer = None
        finally: _lock.release()
        return True
    return False
