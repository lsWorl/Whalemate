from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """应用启动时需要用到的配置。

    这个 dataclass 只负责承载配置，不负责读取环境变量。这样做的好处是：
    1. 读取配置的逻辑集中在 load_settings()。
    2. 其他模块只需要依赖明确的 Settings 字段，不需要到处调用 os.getenv。
    """

    app_name: str
    database_path: Path
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_timeout_seconds: int
    speech_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    speech_api_key: str = ""
    asr_model: str = "glm-asr-2512"
    tts_model: str = "glm-tts"
    tts_provider: str = "system"
    qwen_base_url: str = "https://maas.qianwenaiapi.com/api/v1"
    qwen_api_key: str = ""
    qwen_tts_model: str = "qwen-audio-3.0-tts-flash"
    qwen_tts_voice: str = "longanlingxi"
    speech_language: str = "zh"
    avatar_preview: str = ""
    character_preset: str = ""
    qwen_original_tts_voice: str = ""
    asr_provider: str = "zhipu"  # 识别服务独立选择，保持旧版本默认行为。
    qwen_asr_model: str = "qwen-audio-3.0-asr-flash"  # 千问识别模型不复用合成模型名。
    storage_paths_file: Path | None = None
    meme_directory: Path | None = None
    migration_source: Path | None = None


def load_settings() -> Settings:
    """从 .env 和系统环境变量读取配置。

    python-dotenv 会自动读取项目根目录下的 .env 文件；如果某个变量没有配置，
    这里会给出适合本地开发的默认值。
    """

    project_root = Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")

    from aicompanion.storage.locations import default_data_root, record_path, read_record, directory, DATABASE_NAME
    path_file = record_path(project_root); paths = read_record(path_file)
    default_directory = directory(paths['memory_root']) if paths.get('memory_root') else default_data_root(project_root)
    memory_directory = directory(paths['memory_root']) if paths.get('memory_root') else default_directory
    filename = paths.get('database_name', DATABASE_NAME)
    if not isinstance(filename, str) or Path(filename).name != filename or not filename:
        raise ValueError('路径记录中的数据库文件名无效')
    # 用户在页面选定的路径优先于旧 .env；API 配置仍沿用原加载方式。
    database_path = memory_directory/filename if paths.get('memory_root') else Path(os.getenv('DATABASE_PATH') or default_directory/DATABASE_NAME)
    if not database_path.is_absolute():
        database_path = project_root / database_path
    meme_directory = directory(paths['meme_root']) if paths.get('meme_root') else default_directory/'memes'
    migration_source = (paths.get('migration') or {}).get('source')
    # 旧配置即使写了系统盘路径，也按本次“不放系统盘”的要求安全复制到默认数据盘。
    if os.name == 'nt' and not paths.get('memory_root') and database_path.drive.casefold() == os.getenv('SystemDrive', 'C:').casefold():
        if database_path.is_file(): migration_source = database_path
        database_path = default_directory/database_path.name
    if not paths.get('memory_root') and not os.getenv('DATABASE_PATH') and getattr(sys, 'frozen', False):
        legacy = Path(os.getenv('LOCALAPPDATA', str(project_root)))/'AICompanion'/'data'/DATABASE_NAME
        if legacy.is_file() and legacy.resolve() != database_path.resolve(): migration_source = legacy
    llm_base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()
    speech_base_url = os.getenv("SPEECH_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    speech_api_key = os.getenv("SPEECH_API_KEY")
    if speech_api_key is None:
        # 仅同一服务主机允许复用密钥，避免切换聊天供应商后把密钥发给另一家。
        llm_host = urlsplit(llm_base_url).hostname
        speech_host = urlsplit(speech_base_url).hostname
        speech_api_key = os.getenv("LLM_API_KEY", "") if llm_host and llm_host == speech_host else ""
    return Settings(
        app_name=os.getenv("APP_NAME", "AICompanion"),
        database_path=database_path,
        llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
        llm_base_url=llm_base_url,
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini").strip(),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.8")),
        llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
        speech_base_url=speech_base_url,
        speech_api_key=speech_api_key.strip(),
        asr_model=os.getenv("ASR_MODEL", "glm-asr-2512"),
        tts_model=os.getenv("TTS_MODEL", "glm-tts"),
        tts_provider=os.getenv("TTS_PROVIDER", "system").strip(),
        qwen_base_url=os.getenv("QWEN_BASE_URL", "https://maas.qianwenaiapi.com/api/v1").rstrip("/"),
        qwen_api_key=os.getenv("QWEN_API_KEY", "").strip(),
        qwen_tts_model=os.getenv("QWEN_TTS_MODEL", "qwen-audio-3.0-tts-flash").strip(),
        qwen_tts_voice=os.getenv("QWEN_TTS_VOICE", "longanlingxi").strip(),
        speech_language=os.getenv("SPEECH_LANGUAGE", "zh").strip(),
        avatar_preview=os.getenv("AVATAR_PREVIEW", "").strip(),
        character_preset=os.getenv("CHARACTER_PRESET", "").strip(),
        qwen_original_tts_voice=os.getenv("QWEN_ORIGINAL_TTS_VOICE", "").strip(),
        asr_provider=os.getenv("ASR_PROVIDER", "zhipu").strip().lower(),
        qwen_asr_model=os.getenv("QWEN_ASR_MODEL", "qwen-audio-3.0-asr-flash").strip(),
        storage_paths_file=path_file,
        meme_directory=meme_directory,
        migration_source=Path(migration_source) if migration_source else None,
    )
