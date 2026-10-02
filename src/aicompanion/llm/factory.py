from __future__ import annotations

from aicompanion.config import Settings
from aicompanion.llm.base import LLMClient
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.llm.openai_compatible_client import OpenAICompatibleClient


def create_llm_client(settings: Settings) -> LLMClient:
    """根据配置创建 LLM 客户端。

    这里是模型供应商切换的唯一入口。后续如果要加 DeepSeek 专用客户端、
    本地 Ollama 客户端，或者别的大模型平台，只需要在这里新增分支。
    """

    if settings.llm_provider == "mock":
        return MockLLMClient()

    if settings.llm_provider == "openai_compatible" or settings.llm_provider == "zhipu_compatible":
        return OpenAICompatibleClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )

    raise ValueError(
        f"未知 LLM_PROVIDER：{settings.llm_provider}。"
        "请使用 mock、openai_compatible 或 zhipu_compatible。"
    )
