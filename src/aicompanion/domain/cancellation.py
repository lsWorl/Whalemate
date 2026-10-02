from __future__ import annotations

from threading import Event


class ReplyCancelled(RuntimeError):
    """取消是正常的对话状态，不作为模型错误展示。"""


class CancellationToken:
    """跨线程传递停止请求；客户端在连接与读取边界检查。"""

    def __init__(self) -> None:
        self.event = Event()

    def cancel(self) -> None:
        self.event.set()

    def check(self) -> None:
        if self.event.is_set():
            raise ReplyCancelled()

