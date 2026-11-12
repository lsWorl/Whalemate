from __future__ import annotations

from collections.abc import Iterator
import time

from aicompanion.llm.base import ChatTurn
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.usage import meter
import json


class MockLLMClient:
    """本地模拟 LLM。

    这个类不调用网络，主要用于：
    1. 没有 API key 时继续开发 UI、数据库、情感系统。
    2. 写测试时得到稳定、可预测的回复。
    3. 和真实 LLMClient 使用同一套接口，方便随时切换。
    """

    model = 'mock'

    def __init__(self, agent_script: list | None = None):
        # 离线验收 agent：按顺序给出的工具调用 [(name, args), ...]，用完后给出最终回复
        self.agent_script = list(agent_script or [])

    def chat(self, messages, tools=None, cancellation=None, temperature=None, max_tokens=None):
        if cancellation: cancellation.check()
        chars = sum(len(str(m.get('content') or '')) for m in messages)
        done = sum(1 for m in messages if m.get('role') == 'tool')
        if tools and done < len(self.agent_script):
            name, args = self.agent_script[done]
            reply = {'content': '', 'tool_calls': [{'id': f'mock_{done}', 'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}]}
        else:
            last = next((m.get('content') for m in reversed(messages) if m.get('role') == 'tool'), '')
            reply = {'content': '【开心】搞定啦！'+(f'（最后一步：{str(last)[:40]}）' if last else '离线模式下我不会真的动手。'), 'tool_calls': []}
        meter.report(self.model, None, chars, len(reply['content'])+20)
        return dict(reply, reasoning='', finish_reason='tool_calls' if reply['tool_calls'] else 'stop', usage=None)

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
            "这是离线测试回复，真实模型可在 .env 中配置。"
        )

    def generate_reply_stream(
        self,
        messages: list[ChatTurn],
        system_prompt: str,
        cancellation: CancellationToken | None = None,
    ) -> Iterator[str]:
        """把 mock 回复拆成小片段，模拟真实 API 的流式输出。"""

        reply = self.generate_reply(messages=messages, system_prompt=system_prompt)
        if '【表情与动作】' in (system_prompt or ''):
            # 角色带表情 / 动作词表时，示范台词标注（离线验收标注解析与执行）
            reply = '【开心】'+reply+'<挥手>'
        for index in range(0, len(reply), 4):
            if cancellation:
                cancellation.check()
            time.sleep(0.03)
            yield reply[index : index + 4]
        meter.report(self.model, None, len(system_prompt or '')+sum(len(m.content) for m in messages), len(reply))
