from __future__ import annotations

from threading import Event, RLock


class ReplyCancelled(RuntimeError):
    """取消是正常的对话状态，不作为模型错误展示。"""


class CancellationToken:
    """跨线程传递停止请求；客户端在连接与读取边界检查。"""

    def __init__(self) -> None:
        self.event = Event()
        self.lock = RLock()
        self.callbacks = []

    def cancel(self) -> None:
        self.event.set()
        with self.lock:
            for callback in self.callbacks:
                try: callback()
                except OSError: pass

    def register(self, callback):
        with self.lock:
            self.callbacks.append(callback)
            if self.event.is_set(): callback()

    def unregister(self, callback):
        with self.lock:
            if callback in self.callbacks: self.callbacks.remove(callback)

    def check(self) -> None:
        if self.event.is_set():
            raise ReplyCancelled()

