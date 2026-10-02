from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from typing import Protocol
from aicompanion.domain.cancellation import CancellationToken


@dataclass(frozen=True)
class ChatTurn:
    """一轮聊天消息。

    role 使用 Chat Completions 常见的角色命名：
    - system：系统提示词，一般由程序生成，不直接显示给用户
    - user：用户发送的消息
    - assistant：AI 角色回复的消息
    """

    role: str
    content: str


class LLMClient(Protocol):
    """所有大模型客户端都要遵守的接口。

    UI 和 ChatController 不关心具体供应商是 OpenAI、DeepSeek、通义千问，
    还是本地 mock；它们只依赖这个统一接口。后续替换模型时，只需要新增
    一个实现类，而不需要重写 UI。
    """

    def generate_reply(self, messages: list[ChatTurn], system_prompt: str) -> str:
        """一次性生成完整回复。主要用于测试或不需要流式输出的场景。"""

    def generate_reply_stream(
        self,
        messages: list[ChatTurn],
        system_prompt: str,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[str]:
        """流式生成回复。

        每 yield 一个字符串片段，UI 就可以追加显示一点内容，从而形成
        “AI 正在打字”的体验。
        """
