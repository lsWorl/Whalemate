"""共用的 HTTP 打开方式：所有带密钥的请求都不跟随重定向。

为什么：urllib 默认会跟随 301/302/303，并把 Authorization 头原样带到新地址。
如果配置的地址被跳转到别的域名，密钥就会发给不相干的服务。项目里原来有三份相同的
NoRedirect，这里合成一份，聊天、语音、搜索、社区抓取都用它。遇到跳转直接按 HTTP 错误处理，
由调用方提示用户检查地址。
"""
from __future__ import annotations

from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    """拒绝跟随任何重定向；urllib 会把 3xx 当成 HTTPError 抛给调用方。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(NoRedirect())


def open_url(request, timeout=None):
    """等价于 urlopen，但不跟随重定向。"""
    return _OPENER.open(request, timeout=timeout)


def fetch(request, cancellation, timeout=20, limit=25 * 1024 * 1024, opener=None) -> bytes:
    """读取完整响应体，随时可取消，超过 limit 字节就停止。

    取消时关闭底层 socket，阻塞中的读取立即返回；HTTPError / URLError 原样抛给调用方，
    由各供应商客户端翻译成不含密钥和请求摘要的提示。语音合成、识别等下载音频的地方共用。
    """
    import socket
    cancellation.check()
    abort = None
    try:
        with (opener.open if opener is not None else open_url)(request, timeout=timeout) as response:
            sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            if sock:
                abort = lambda: sock.shutdown(socket.SHUT_RDWR)
                cancellation.register(abort)
            chunks, size = [], 0
            while True:
                cancellation.check()
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ResponseTooLarge('响应超过大小限制')
                chunks.append(chunk)
            cancellation.check()
            return b''.join(chunks)
    finally:
        if abort:
            cancellation.unregister(abort)


class ResponseTooLarge(RuntimeError):
    pass
