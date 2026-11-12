"""收音键：在任何窗口里按住都能说话。Windows 用 GetAsyncKeyState 轮询，不装全局钩子、不拦截按键。"""
from __future__ import annotations

import sys

# 选项：显示名 → 虚拟键码
KEYS = {
    'RCtrl': ('右 Ctrl', 0xA3),
    'RAlt': ('右 Alt', 0xA5),
    'F8': ('F8', 0x77),
    'F9': ('F9', 0x78),
    'CapsLock': ('Caps Lock', 0x14),
    'XButton1': ('鼠标侧键（后退）', 0x05),
}
DEFAULT_KEY = 'RCtrl'


def label(name: str) -> str:
    return KEYS.get(name, KEYS[DEFAULT_KEY])[0]


def key_reader(name: str):
    """返回一个无参函数：该键此刻是否按着。非 Windows 返回始终 False。"""
    code = KEYS.get(name, KEYS[DEFAULT_KEY])[1]
    if sys.platform != 'win32':
        return lambda: False
    import ctypes
    get = ctypes.windll.user32.GetAsyncKeyState
    return lambda: bool(get(code) & 0x8000)
