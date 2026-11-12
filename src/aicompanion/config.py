from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

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
    # 聊天、识图、搜索、办事用哪个模型由设置页的模型配置决定（计划书 v4 功能一），不再从 .env 读 LLM_*。
    # offline=True 时一律用离线模拟模型（发布自检、AICOMPANION_OFFLINE=1）。
    offline: bool = False
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
    speech_base_url = os.getenv("SPEECH_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    return Settings(
        app_name=os.getenv("APP_NAME", "AICompanion"),
        database_path=database_path,
        offline=os.getenv("AICOMPANION_OFFLINE", "").strip() in ("1", "true", "yes"),
        speech_base_url=speech_base_url,
        # 语音 Key 独立填写，不再按主机名借用聊天 Key（硬底线：凭据不串）
        speech_api_key=os.getenv("SPEECH_API_KEY", "").strip(),
        asr_model=os.getenv("ASR_MODEL", "glm-asr-2512"),
        tts_model=os.getenv("TTS_MODEL", "glm-tts"),
        tts_provider=os.getenv("TTS_PROVIDER", "system").strip(),
        qwen_base_url=os.getenv("QWEN_BASE_URL", "https://maas.qianwenaiapi.com/api/v1").rstrip("/"),
        qwen_api_key=os.getenv("QWEN_API_KEY", "").strip(),
        qwen_tts_model=os.getenv("QWEN_TTS_MODEL", "qwen-audio-3.0-tts-flash").strip(),
        qwen_tts_voice=os.getenv("QWEN_TTS_VOICE", "longanlingxi").strip(),
        speech_language=os.getenv("SPEECH_LANGUAGE", "zh").strip(),
        qwen_original_tts_voice=os.getenv("QWEN_ORIGINAL_TTS_VOICE", "").strip(),
        asr_provider=os.getenv("ASR_PROVIDER", "zhipu").strip().lower(),
        qwen_asr_model=os.getenv("QWEN_ASR_MODEL", "qwen-audio-3.0-asr-flash").strip(),
        storage_paths_file=path_file,
        meme_directory=meme_directory,
        migration_source=Path(migration_source) if migration_source else None,
    )
