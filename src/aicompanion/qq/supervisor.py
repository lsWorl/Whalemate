"""启动和结束我们自己的后台进程（NapCat、AstrBot）。

- 无窗口、独立进程组，输出写到 <根>/logs/<名字>.log，不阻塞界面；
- pid 记在 manifest 里；结束时先确认这个 pid 的程序文件确实在我们的目录里（桌宠重启后 pid 可能被别的程序复用），
  再连同子进程一起结束。不碰任何不是我们启动的进程。
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

IS_WINDOWS = os.name == 'nt'


def process_path(pid: int) -> str | None:
    """pid 对应程序文件的完整路径；进程不存在返回 None。"""
    if not pid: return None
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle: return None
        try:
            code = wintypes.DWORD()
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259: return None   # 已退出
            size = wintypes.DWORD(1024); buffer = ctypes.create_unicode_buffer(1024)
            return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ''
        finally:
            kernel.CloseHandle(handle)
    try: return os.readlink(f'/proc/{int(pid)}/exe')
    except OSError:
        try: os.kill(int(pid), 0); return ''
        except OSError: return None


class Supervisor:
    def __init__(self, layout, path_of=process_path, popen=subprocess.Popen):
        self.layout, self.path_of, self.popen = layout, path_of, popen
        self.children: dict[str, object] = {}   # 本次会话启动的 Popen

    def _records(self) -> dict:
        return dict(self.layout.manifest().get('processes') or {})

    def ours(self, name: str) -> int | None:
        """名字对应的、仍在运行且程序文件在我们目录里的 pid。"""
        record = self._records().get(name) or {}
        pid = record.get('pid')
        path = self.path_of(pid) if pid else None
        if path is None: return None
        if path and not self.layout.owns(Path(path)): return None   # pid 被别的程序复用了
        return pid

    def running(self, name: str) -> bool:
        child = self.children.get(name)
        if child is not None and child.poll() is None: return True
        return self.ours(name) is not None

    def start(self, name: str, args: list, cwd: Path, env: dict) -> int:
        if self.running(name): return self.ours(name) or self.children[name].pid
        self.layout.logs.mkdir(parents=True, exist_ok=True)
        log = open(self.layout.logs/f'{name}.log', 'ab')
        flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)) if IS_WINDOWS else 0
        extra = {} if IS_WINDOWS else {'start_new_session': True}
        try:
            child = self.popen([str(a) for a in args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, creationflags=flags, **extra)
        finally:
            log.close()
        self.children[name] = child
        records = self._records(); records[name] = {'pid': child.pid, 'started': time.time(), 'exe': str(args[0])}
        self.layout.update_manifest(processes=records)
        return child.pid

    def stop(self, name: str, timeout: float = 10) -> bool:
        pid = self.ours(name)
        child = self.children.pop(name, None)
        if pid is None and (child is None or child.poll() is not None):
            self._forget(name); return False
        pid = pid or child.pid
        try:
            if IS_WINDOWS:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=timeout,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                import signal
                os.killpg(pid, signal.SIGTERM)
        except Exception:
            pass
        if child is not None:
            try: child.wait(timeout=timeout)
            except Exception: pass
        self._forget(name)
        return True

    def _forget(self, name):
        records = self._records()
        if name in records: records.pop(name); self.layout.update_manifest(processes=records)

    def exited_with(self, name: str):
        """本次启动的进程如果已经退出，返回退出码；还在跑或不是本次启动的返回 None。"""
        child = self.children.get(name)
        return child.poll() if child is not None else None

    def tail(self, name: str, lines: int = 20, secrets=()) -> str:
        """日志最后几行（诊断用）。上游程序自己可能把令牌打进日志（例如 NapCat 打印带 token 的面板地址），这里显示前替换掉。"""
        try: data = (self.layout.logs/f'{name}.log').read_bytes()[-8000:].decode('utf-8', 'replace')
        except OSError: return ''
        for secret in secrets:
            if secret: data = data.replace(secret, '***')
        return '\n'.join(data.splitlines()[-lines:])
