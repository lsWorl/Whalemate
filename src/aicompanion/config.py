from __future__ import annotations

import os
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


def load_settings() -> Settings:
    """从 .env 和系统环境变量读取配置。

    python-dotenv 会自动读取项目根目录下的 .env 文件；如果某个变量没有配置，
    这里会给出适合本地开发的默认值。
    """

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")

    database_path = Path(os.getenv("DATABASE_PATH", "data/aicompanion.sqlite3"))
    if not database_path.is_absolute():
        database_path = project_root / database_path
    return Settings(
        app_name=os.getenv("APP_NAME", "AICompanion"),
        database_path=database_path,
        llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini").strip(),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.8")),
        llm_timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
        speech_base_url=os.getenv("SPEECH_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/"),
        speech_api_key=os.getenv("SPEECH_API_KEY", os.getenv("LLM_API_KEY", "")).strip(),
        asr_model=os.getenv("ASR_MODEL", "glm-asr-2512"),
        tts_model=os.getenv("TTS_MODEL", "glm-tts"),
    )
