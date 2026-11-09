"""启动和结束我们自己的后台进程（NapCat、AstrBot）。

- 无窗口、独立进程组，输出写到 <根>/logs/<名字>.log，不阻塞界面；
- pid、程序文件、创建时间记在 manifest 里；跨会话结束时三者都核对上才动手（桌宠重启后 pid 可能被别的程序复用，
  同一目录里的别的程序也不算），查不到身份就不动。不碰任何不是我们启动的进程。
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

IS_WINDOWS = os.name == 'nt'


def process_identity(pid: int):
    """(程序文件完整路径, 创建时间) ；进程不存在返回 None；存在但查不到身份返回 ('', 0)（调用方必须当作"不是我们的"）。"""
    if not pid: return None
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),)*4)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None if ctypes.get_last_error() == 87 else ('', 0)   # 87 = 参数无效：没有这个进程；其他错误 = 有但查不到
        try:
            code = wintypes.DWORD()
            if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259: return None   # 已退出
            size = wintypes.DWORD(1024); buffer = ctypes.create_unicode_buffer(1024)
            path = buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else ''
            times = [wintypes.FILETIME() for _ in range(4)]
            created = (times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) if kernel.GetProcessTimes(handle, *map(ctypes.byref, times)) else 0
            return (path, created)
        finally:
            kernel.CloseHandle(handle)
    try:
        path = os.readlink(f'/proc/{int(pid)}/exe')
        created = int(open(f'/proc/{int(pid)}/stat').read().rsplit(')', 1)[1].split()[19])
        return (path, created)
    except FileNotFoundError:
        return None
    except OSError:
        return ('', 0)


def same_file(a: str, b: str) -> bool:
    try: return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError): return False


class Supervisor:
    def __init__(self, layout, identity_of=process_identity, popen=subprocess.Popen, kill=None):
        self.layout, self.identity_of, self.popen = layout, identity_of, popen
        self.kill = kill or self._kill_tree
        self.children: dict[str, object] = {}   # 本次会话启动的 Popen

    def _records(self) -> dict:
        return dict(self.layout.manifest().get('processes') or {})

    def ours(self, name: str) -> int | None:
        """上一次会话记录的、仍在运行且确认是同一个进程的 pid：程序文件和记录的完全一致（不只是在同一个目录）、
        创建时间也一致。查不到身份、对不上的一律当作不是我们的（pid 可能已被别的程序复用）。"""
        record = self._records().get(name) or {}
        pid, exe, created = record.get('pid'), record.get('exe'), record.get('created')
        if not pid or not exe or not created: return None
        identity = self.identity_of(pid)
        if not identity: return None
        path, actual = identity
        if not path or not actual: return None
        if not same_file(path, exe) or not self.layout.owns(Path(path)) or int(actual) != int(created): return None
        return pid

    def running(self, name: str) -> bool:
        child = self.children.get(name)
        if child is not None and child.poll() is None: return True
        return self.ours(name) is not None

    def start(self, name: str, args: list, cwd: Path, env: dict) -> int:
        if self.running(name): return self.ours(name) or self.children[name].pid
        self.layout.logs.mkdir(parents=True, exist_ok=True)
        log = open(self.layout.logs/f'{name}.log', 'ab')
        log_start = log.tell()
        flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)) if IS_WINDOWS else 0
        extra = {} if IS_WINDOWS else {'start_new_session': True}
        try:
            child = self.popen([str(a) for a in args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, creationflags=flags, **extra)
        finally:
            log.close()
        self.children[name] = child
        identity = self.identity_of(child.pid) or ('', 0)
        records = self._records()
        records[name] = {'pid': child.pid, 'started': time.time(), 'exe': identity[0] or str(args[0]), 'created': identity[1], 'log_start': log_start}
        self.layout.update_manifest(processes=records)
        return child.pid

    def stop(self, name: str, timeout: float = 10) -> bool:
        """结束我们的进程（连同子进程）。本次会话持有的 Popen 直接按它结束；跨会话的只结束身份核对通过的。
        确认真的退出了才清掉记录；没退出返回 False 并保留记录，下次还能再试。"""
        child = self.children.get(name)
        if child is not None and child.poll() is None:
            pid, alive = child.pid, lambda: child.poll() is None
        else:
            pid = self.ours(name)
            if pid is None:   # 已经没在跑，或者无法确认是我们的：不动它，只清掉过期记录
                self.children.pop(name, None)
                if self.identity_of((self._records().get(name) or {}).get('pid')) is None: self._forget(name)
                return False
            alive = lambda: self.ours(name) is not None
        self.kill(pid, timeout)
        deadline = time.monotonic()+timeout
        while alive() and time.monotonic() < deadline: time.sleep(0.2)
        if alive(): return False
        self.children.pop(name, None); self._forget(name)
        return True

    @staticmethod
    def _kill_tree(pid, timeout):
        try:
            if IS_WINDOWS:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=timeout,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                import signal
                os.killpg(pid, signal.SIGTERM)
        except Exception:
            pass

    def _forget(self, name):
        records = self._records()
        if name in records: records.pop(name); self.layout.update_manifest(processes=records)

    def exited_with(self, name: str):
        """本次启动的进程如果已经退出，返回退出码；还在跑或不是本次启动的返回 None。"""
        child = self.children.get(name)
        return child.poll() if child is not None else None

    def tail(self, name: str, lines: int = 20, secrets=()) -> str:
        """日志最后几行（诊断用）。上游程序自己可能把令牌打进日志（例如 NapCat 打印带 token 的面板地址），这里显示前替换掉。"""
        try:
            # 保留完整日志，但本次启动失败只展示本次输出，避免混入旧聊天或旧插件的发送记录。
            offset = (self._records().get(name) or {}).get('log_start', 0)
            if not isinstance(offset, int) or offset < 0: offset = 0
            with (self.layout.logs/f'{name}.log').open('rb') as log:
                size = os.fstat(log.fileno()).st_size
                log.seek(max(offset if offset <= size else 0, size-8000))
                data = log.read(8000).decode('utf-8', 'replace')
        except OSError: return ''
        for secret in secrets:
            if secret: data = data.replace(secret, '***')
        return '\n'.join(data.splitlines()[-lines:])
