"""Windows 桌面集成：置顶守护、全屏检测、系统闲置时间、开机自启。

非 Windows 平台上全部安全降级为"无操作"，便于开发和测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

IS_WINDOWS = sys.platform == 'win32'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'AICompanion'

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0001, 0x0002, 0x0010, 0x0200
    MONITOR_DEFAULTTONEAREST = 2

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]

    try:
        dwmapi = ctypes.windll.dwmapi
    except OSError:
        dwmapi = None
    DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
    DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND = 33, 1
    DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE = 34, 0xFFFFFFFE

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetShellWindow.restype = wintypes.HWND
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    kernel32.GetTickCount.restype = wintypes.DWORD


def remove_window_frame(widget) -> None:
    """去掉 Windows 11 给顶层窗口自动加的圆角描边、细边框和阴影，让透明窗口真正"无框"。

    Windows 10 不支持的属性会被系统忽略；任何失败都不影响显示。
    """
    if not IS_WINDOWS or dwmapi is None: return
    try:
        hwnd = wintypes.HWND(int(widget.winId()))
        for attribute, value in ((DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED),
                                 (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND),
                                 (DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE)):
            data = wintypes.DWORD(value)
            dwmapi.DwmSetWindowAttribute(hwnd, wintypes.DWORD(attribute), ctypes.byref(data), ctypes.sizeof(data))
    except Exception:
        pass


GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED = -20, 0x00000020, 0x00080000


def set_click_through(widget, enabled: bool) -> bool:
    """只改原生窗口的 WS_EX_TRANSPARENT，让鼠标穿透或停止穿透；返回 True 表示已处理。

    T27：以前用 QWindow.setFlag(WindowTransparentForInput) 切换。Qt 会按新标志重算整套扩展样式，
    分层窗口在重新上屏前会有一帧露出黑底，表现为整个桌宠窗口闪一下黑色方框。这里直接改一位，
    不碰 WS_EX_LAYERED 和其他样式。窗口还不是分层窗口时（例如没有透明背景）返回 False，交给 Qt 处理。
    """
    if not IS_WINDOWS: return False
    try:
        hwnd = wintypes.HWND(int(widget.winId()))
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_LAYERED: return False
        wanted = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
        if wanted != style:
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, wanted)
        return True
    except Exception:
        return False


def ensure_topmost(widget) -> None:
    """把窗口重新放回最顶层但不抢焦点。Qt 的置顶标志在 Windows 上可能被其他置顶窗口压下去。"""
    if not IS_WINDOWS or not widget.isVisible(): return
    try:
        user32.SetWindowPos(wintypes.HWND(int(widget.winId())), HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
    except Exception:
        pass


def foreground_is_fullscreen(own_handles=()) -> bool:
    """前台窗口是否占满整块显示器（游戏、全屏视频、演示）。桌面本身和本应用窗口不算。"""
    if not IS_WINDOWS: return False
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd == user32.GetShellWindow() or int(hwnd) in {int(h) for h in own_handles}: return False
        name = ctypes.create_unicode_buffer(64); user32.GetClassNameW(hwnd, name, 64)
        if name.value in ('Progman', 'WorkerW', 'Shell_TrayWnd'): return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)): return False
        info = MONITORINFO(); info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info)): return False
        m = info.rcMonitor
        return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    except Exception:
        return False


def system_idle_seconds() -> float:
    """距离用户最后一次键盘/鼠标操作的秒数（整个系统，不只是本应用）。"""
    if not IS_WINDOWS: return 0.0
    try:
        info = LASTINPUTINFO(); info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)): return 0.0
        return max(0, (kernel32.GetTickCount()-info.dwTime) & 0xFFFFFFFF)/1000.0
    except Exception:
        return 0.0


def autostart_command(executable: str = sys.executable, frozen: bool = bool(getattr(sys, 'frozen', False)), main_script: Path | None = None) -> str:
    """开机自启命令：打包版直接运行 exe；开发版用 pythonw 运行 main.py，不弹控制台窗口。"""
    if frozen: return f'"{executable}"'
    exe = Path(executable)
    pythonw = exe.with_name('pythonw.exe') if exe.name.lower() == 'python.exe' else exe
    script = main_script or Path(__file__).resolve().parents[3]/'main.py'
    return f'"{pythonw}" "{script}"'


def autostart_enabled() -> bool:
    if not IS_WINDOWS: return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME); return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """写入或删除当前用户的开机启动项。返回是否成功。"""
    if not IS_WINDOWS: return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled: winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try: winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError: pass
        return True
    except OSError:
        return False
