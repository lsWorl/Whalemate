"""凭据保存：数据库里只放密文和修订号，明文只在发请求的那一刻取出。

引用格式：
- env:NAME      读环境变量（兼容旧 .env；程序启动时由 python-dotenv 载入）；
- stored:NAME   用户在设置页保存的 Key，用 Windows DPAPI 按当前用户加密后存进 app_settings。

D 盘是 exFAT，没有文件权限，所以不能指望目录权限保护明文；DPAPI 让密文只能由同一个 Windows
用户解开。不是 Windows（开发容器、测试）时没有安全后端，保存会明确失败，不会退化成明文；测试注入假的加解密器。
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys

KEY_PREFIX = 'credential:'
ENTROPY = b'AICompanion-credential-v1'


class CredentialError(RuntimeError):
    pass


class WindowsDpapi:
    """当前 Windows 用户的 DPAPI。只依赖标准库 ctypes，不引入新依赖。"""
    name = 'dpapi'

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes

        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
        self.Blob = Blob
        self.crypt32 = ctypes.windll.crypt32
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _blob(self, data: bytes):
        buffer = self.ctypes.create_string_buffer(data, len(data))
        return self.Blob(len(data), self.ctypes.cast(buffer, self.ctypes.POINTER(self.ctypes.c_char))), buffer

    def _call(self, function, data: bytes) -> bytes:
        ctypes = self.ctypes
        source, keep = self._blob(data)
        entropy, keep_entropy = self._blob(ENTROPY)
        out = self.Blob()
        # 0x1 = CRYPTPROTECT_UI_FORBIDDEN：不弹任何系统对话框
        if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)):
            raise CredentialError('系统凭据加解密失败')
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            self.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def protect(self, data: bytes) -> bytes:
        return self._call(lambda s, d, e, r, p, f, o: self.crypt32.CryptProtectData(s, 'AICompanion', e, r, p, f, o), data)

    def unprotect(self, data: bytes) -> bytes:
        return self._call(self.crypt32.CryptUnprotectData, data)


def default_protector():
    if sys.platform == 'win32':
        try:
            return WindowsDpapi()
        except (OSError, AttributeError):
            return None
    return None


class CredentialStore:
    def __init__(self, database, environ=None, protector='auto'):
        self.db = database
        self.environ = os.environ if environ is None else environ
        self.protector = default_protector() if protector == 'auto' else protector

    # ---- 读取 -----------------------------------------------------------------------------
    def get(self, ref: str) -> str:
        """取出明文；不存在或解不开返回空串。调用方只在构造请求时使用，不要保存或记录。"""
        kind, _, name = str(ref or '').partition(':')
        if kind == 'env':
            return str(self.environ.get(name, '') or '').strip()
        if kind == 'stored':
            record = self.db.get_setting(KEY_PREFIX + name)
            if not isinstance(record, dict) or not record.get('blob') or self.protector is None:
                return ''
            try:
                return self.protector.unprotect(base64.b64decode(record['blob'])).decode('utf-8')
            except Exception:
                return ''   # 换了 Windows 用户或密文损坏：当作没有，界面提示重新填写
        return ''

    def available(self, ref: str) -> bool:
        return bool(self.get(ref))

    def revision(self, ref: str) -> str:
        """凭据的修订号。换了 Key 就会变，客户端缓存据此重建；不包含可还原的明文。"""
        kind, _, name = str(ref or '').partition(':')
        if kind == 'env':
            value = self.get(ref)
            return 'env:' + (hashlib.sha256(value.encode()).hexdigest()[:12] if value else 'empty')
        if kind == 'stored':
            record = self.db.get_setting(KEY_PREFIX + name)
            return f"stored:{int(record.get('revision', 0)) if isinstance(record, dict) else 0}"
        return 'none'

    def describe(self, ref: str) -> str:
        """给界面看的说明：只说来源和是否可用，从不显示 Key 的任何一部分。"""
        kind, _, name = str(ref or '').partition(':')
        state = '可用' if self.available(ref) else '未设置'
        if kind == 'env': return f'环境变量 {name}（{state}）'
        if kind == 'stored': return f'已加密保存（{state}）'
        return '未设置'

    # ---- 写入 -----------------------------------------------------------------------------
    def save(self, name: str, secret: str) -> str:
        """保存或替换一把 Key，返回引用。空白输入不会清掉原有 Key（防止误点保存）。"""
        name = str(name or '').strip()
        secret = str(secret or '').strip()
        if not name: raise CredentialError('凭据名称不能为空')
        if not secret: raise CredentialError('Key 为空，未做修改')
        if self.protector is None:
            raise CredentialError('当前系统没有可用的安全存储，Key 未保存；可以继续使用 .env 里的环境变量')
        previous = self.db.get_setting(KEY_PREFIX + name)
        revision = int(previous.get('revision', 0)) + 1 if isinstance(previous, dict) else 1
        blob = base64.b64encode(self.protector.protect(secret.encode('utf-8'))).decode('ascii')
        self.db.set_setting(KEY_PREFIX + name, {'blob': blob, 'backend': getattr(self.protector, 'name', 'custom'),
                                                'revision': revision})
        return 'stored:' + name

    def delete(self, name: str) -> None:
        """删除保存的 Key：留下空记录并递增修订号，正在使用它的客户端会在下次请求时失效。"""
        previous = self.db.get_setting(KEY_PREFIX + name)
        revision = int(previous.get('revision', 0)) + 1 if isinstance(previous, dict) else 1
        self.db.set_setting(KEY_PREFIX + name, {'blob': '', 'revision': revision})
