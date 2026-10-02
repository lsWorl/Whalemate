from __future__ import annotations

from collections.abc import Iterator
import time

from aicompanion.llm.base import ChatTurn
from aicompanion.domain.cancellation import CancellationToken


class MockLLMClient:
    """本地模拟 LLM。

    这个类不调用网络，主要用于：
    1. 没有 API key 时继续开发 UI、数据库、情感系统。
    2. 写测试时得到稳定、可预测的回复。
    3. 和真实 LLMClient 使用同一套接口，方便随时切换。
    """

    def generate_reply(self, messages: list[ChatTurn], system_prompt: str) -> str:
        last_user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        if not last_user_message:
            return "我在这里。今天也想陪你慢慢把事情做好。"

        return (
            "我听到啦。你刚才说："
            f"{last_user_message}\n\n"
            "现在我还是本地模拟回复，下一步我们会把这里替换成真正的大模型 API。"
        )

    def generate_reply_stream(
        self,
        messages: list[ChatTurn],
        system_prompt: str,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[str]:
        """把 mock 回复拆成小片段，模拟真实 API 的流式输出。"""

        reply = self.generate_reply(messages=messages, system_prompt=system_prompt)
        for index in range(0, len(reply), 4):
            if cancellation:
                cancellation.check()
            time.sleep(0.03)
            yield reply[index : index + 4]
