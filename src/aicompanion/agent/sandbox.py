"""低完整性运行：DSH 运行时和她跑的命令都用 Windows 低完整性令牌启动（和浏览器沙箱同一机制）。

效果（实测）：C 盘这类 NTFS 盘上，系统目录、用户目录、浏览器数据、.ssh 等她都写不进去。
局限（实测）：exFAT / FAT 盘没有任何权限信息，低完整性在那里不起作用——所以改文件、删文件、跑命令另外由
tools_server.py 在执行前把关；这里只是第二道保险。非 Windows 平台上什么都不做。
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading

IS_WINDOWS = sys.platform == 'win32'


def low_root() -> Path:
    """%USERPROFILE%\\AppData\\LocalLow\\AICompanion：系统自带"低"标签，低完整性进程天然可写。"""
    return Path(os.getenv('USERPROFILE') or Path.home())/'AppData'/'LocalLow'/'AICompanion'


# ---- 低完整性启动子进程 -------------------------------------------------------------------------
_local = threading.local()
_installed = False


def _create_process_low(executable, args, proc_attrs, thread_attrs, inherit_handles, flags, env, cwd, startupinfo):
    """与 _winapi.CreateProcess 同签名，但用低完整性令牌（CreateProcessAsUserW）。返回 (hProcess, hThread, pid, tid)。"""
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True); kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE

    class SID_AND_ATTRIBUTES(ctypes.Structure): _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wintypes.DWORD)]
    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                    ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD), ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
                    ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD),
                    ('dwFlags', wintypes.DWORD), ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                    ('lpReserved2', ctypes.c_void_p), ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]
    class STARTUPINFOEXW(ctypes.Structure): _fields_ = [('StartupInfo', STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]
    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE), ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]

    # 所有调用都声明参数类型：句柄在 64 位上超出 int 范围，不声明会 OverflowError
    P, H, D, B = ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [H, D, P]; advapi.OpenProcessToken.restype = B
    advapi.DuplicateTokenEx.argtypes = [H, D, P, ctypes.c_int, ctypes.c_int, P]; advapi.DuplicateTokenEx.restype = B
    advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, P]; advapi.ConvertStringSidToSidW.restype = B
    advapi.SetTokenInformation.argtypes = [H, ctypes.c_int, P, D]; advapi.SetTokenInformation.restype = B
    advapi.GetLengthSid.argtypes = [P]; advapi.GetLengthSid.restype = D
    kernel.InitializeProcThreadAttributeList.argtypes = [P, D, D, ctypes.POINTER(ctypes.c_size_t)]; kernel.InitializeProcThreadAttributeList.restype = B
    kernel.UpdateProcThreadAttribute.argtypes = [P, D, ctypes.c_size_t, P, ctypes.c_size_t, P, P]; kernel.UpdateProcThreadAttribute.restype = B
    kernel.DeleteProcThreadAttributeList.argtypes = [P]; kernel.DeleteProcThreadAttributeList.restype = None
    kernel.LocalFree.argtypes = [P]; kernel.LocalFree.restype = P
    kernel.CloseHandle.argtypes = [H]; kernel.CloseHandle.restype = B
    token, low = wintypes.HANDLE(), wintypes.HANDLE()
    # TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ADJUST_DEFAULT
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x1 | 0x2 | 0x8 | 0x80, ctypes.byref(token)): raise ctypes.WinError(ctypes.get_last_error())
    sid = ctypes.c_void_p(); attribute_list = None
    try:
        if not advapi.DuplicateTokenEx(token, 0x02000000, None, 2, 1, ctypes.byref(low)): raise ctypes.WinError(ctypes.get_last_error())
        if not advapi.ConvertStringSidToSidW(ctypes.c_wchar_p('S-1-16-4096'), ctypes.byref(sid)): raise ctypes.WinError(ctypes.get_last_error())
        label = SID_AND_ATTRIBUTES(sid.value, 0x20)   # SE_GROUP_INTEGRITY
        if not advapi.SetTokenInformation(low, 25, ctypes.addressof(label), ctypes.sizeof(label)+advapi.GetLengthSid(sid)):
            raise ctypes.WinError(ctypes.get_last_error())

        info = STARTUPINFOEXW(); info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOW)
        if startupinfo is not None:
            info.StartupInfo.dwFlags = startupinfo.dwFlags; info.StartupInfo.wShowWindow = startupinfo.wShowWindow
            for name in ('hStdInput', 'hStdOutput', 'hStdError'):
                value = getattr(startupinfo, name, None)
                if value not in (None, -1): setattr(info.StartupInfo, name, int(value))
            handles = list((getattr(startupinfo, 'lpAttributeList', None) or {}).get('handle_list') or [])
            if handles:
                # 只让子进程继承这几个管道句柄（和 subprocess 原本的行为一致）
                size = ctypes.c_size_t()
                kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                attribute_list = ctypes.create_string_buffer(size.value)
                if not kernel.InitializeProcThreadAttributeList(ctypes.cast(attribute_list, ctypes.c_void_p), 1, 0, ctypes.byref(size)): raise ctypes.WinError(ctypes.get_last_error())
                array = (wintypes.HANDLE*len(handles))(*[int(h) for h in handles])
                info._handles = array
                if not kernel.UpdateProcThreadAttribute(ctypes.cast(attribute_list, ctypes.c_void_p), 0, 0x20002, ctypes.cast(array, ctypes.c_void_p), ctypes.sizeof(array), None, None): raise ctypes.WinError(ctypes.get_last_error())
                info.lpAttributeList = ctypes.cast(attribute_list, ctypes.c_void_p)
                info.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW); flags |= 0x80000   # EXTENDED_STARTUPINFO_PRESENT
        block = None
        if env is not None:
            text = ''.join(f'{k}={v}\0' for k, v in sorted(dict(env).items(), key=lambda kv: kv[0].upper()))+'\0'
            block = ctypes.create_unicode_buffer(text, len(text)); flags |= 0x400   # CREATE_UNICODE_ENVIRONMENT
        command = ctypes.create_unicode_buffer(str(args))
        process = PROCESS_INFORMATION()
        advapi.CreateProcessAsUserW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                                wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]
        advapi.CreateProcessAsUserW.restype = B
        if not advapi.CreateProcessAsUserW(low, executable, command, None, None, bool(inherit_handles), flags, ctypes.cast(block, ctypes.c_void_p) if block is not None else None,
                                           None if cwd is None else str(cwd), ctypes.byref(info), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(process.hProcess or 0), int(process.hThread or 0), process.dwProcessId, process.dwThreadId
    finally:
        if attribute_list is not None: kernel.DeleteProcThreadAttributeList(ctypes.cast(attribute_list, ctypes.c_void_p))
        if sid: kernel.LocalFree(sid)
        for h in (token, low):
            if h: kernel.CloseHandle(h)


def _install():
    """替换 subprocess 用的 CreateProcess：只有在 low_integrity() 里发起的启动才降级，其他照常。"""
    global _installed
    if _installed or not IS_WINDOWS: return
    import _winapi
    original = _winapi.CreateProcess

    def create_process(*args):
        if getattr(_local, 'low', False): return _create_process_low(*args)
        return original(*args)
    subprocess._winapi.CreateProcess = create_process
    _installed = True


class low_integrity:
    """with low_integrity(): subprocess.Popen(...) —— 在这个线程里启动的子进程是低完整性的。"""

    def __enter__(self):
        _install(); _local.low = True; return self

    def __exit__(self, *exc):
        _local.low = False


def child_env(base: dict | None = None) -> dict:
    """低完整性进程的临时目录放到 LocalLow（普通 %TEMP% 它写不进去）。"""
    temp = low_root()/'temp'; temp.mkdir(parents=True, exist_ok=True)
    env = dict(base or {}); env.update({'TEMP': str(temp), 'TMP': str(temp)})
    return env
