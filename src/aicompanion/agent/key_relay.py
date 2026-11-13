"""MiniMax 办事的本机 Key 中转：Harness 只拿到一次性令牌，真 Key 留在桌宠进程里，上游跳转一律拦截。

为什么要有它（Codex PR #32 审计 P1，实测复现）：Harness 的 llm-pi-ai 路由用打包运行时自带的 fetch 发请求，
默认跟随跳转，跨主机 307 时 `x-api-key` 原样带到新地址；补丁和 YAML 配置都没有关掉跟随跳转的开关。
补丁里只限定初始 baseURL 不够，跳转发生在 Harness 子进程内部，Python 这边的禁止跳转管不到它。

做法：
- 补丁里的 baseURL 改指本机 127.0.0.1 上的这个中转，子进程环境变量里的“Key”换成每次启动随机生成的令牌；
- 中转核对令牌后，把令牌换成真 Key，用项目统一的不跟随跳转的打开方式（net.open_url）转发到账户主机；
- 上游返回 3xx 时不转交 Location，直接回 502，Key 不会去任何别的地址；
- 只转发账户地址路径（/anthropic）下的请求，响应按块流式转回（SSE）。

令牌只在本机回环口有效，而且只能换来对账户主机的请求；Harness 进程从头到尾不持有真 Key。
"""
from __future__ import annotations

import hmac
import http.server
import json
import secrets
import socket
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from aicompanion import net

# 不往上游转的请求头：逐跳头、主机、长度（重新计算）、压缩（要原样流式转回 SSE）、两种鉴权头（换成真 Key）
_DROP_REQUEST = {'host', 'connection', 'keep-alive', 'proxy-connection', 'proxy-authorization', 'te', 'trailer', 'upgrade',
                 'transfer-encoding', 'content-length', 'accept-encoding', 'x-api-key', 'authorization'}
# 不往回转的响应头：逐跳头、长度（改为分块）、跳转地址、Cookie
_DROP_RESPONSE = {'connection', 'keep-alive', 'transfer-encoding', 'content-length', 'location', 'set-cookie', 'trailer', 'upgrade'}
MAX_BODY = 64*1024*1024        # 请求体上限（带图片的对话也够用）
UPSTREAM_TIMEOUT = 600          # 单次读取的超时（秒）；Harness 自己还有流空闲超时


def _error(message: str) -> bytes:
    """Anthropic 兼容的错误体，Harness 能把 message 原样显示出来。"""
    return json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': message}}, ensure_ascii=False).encode('utf-8')


class KeyRelay:
    """一条路由一个中转；和 Harness 运行时同生共死（运行时重启时一起换，令牌也换）。"""

    def __init__(self, upstream: str, api_key: str, opener=None):
        parts = urlsplit(upstream)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ValueError('办事的接口地址无效')
        self.origin = f'{parts.scheme}://{parts.netloc}'
        self.prefix = parts.path.rstrip('/') or ''
        self._key = api_key
        self.token = secrets.token_urlsafe(32)
        self._open = opener or net.open_url
        relay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_POST(self): relay._handle(self)
            def do_GET(self): relay._handle(self)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.base_url = f'http://127.0.0.1:{self.port}{self.prefix}'
        threading.Thread(target=self.server.serve_forever, daemon=True, name='agent-key-relay').start()

    def close(self):
        try: self.server.shutdown(); self.server.server_close()
        except Exception: pass

    # ---- 处理一个请求 -------------------------------------------------------------
    def _authorized(self, headers) -> str:
        """令牌对上了就返回 Harness 用的鉴权方式（x-api-key / bearer），否则空串。"""
        key = headers.get('x-api-key') or ''
        if key and hmac.compare_digest(key.encode(), self.token.encode()): return 'x-api-key'
        auth = headers.get('authorization') or ''
        if auth[:7].lower() == 'bearer ' and hmac.compare_digest(auth[7:].strip().encode(), self.token.encode()): return 'bearer'
        return ''

    def _reply(self, handler, status: int, body: bytes):
        handler.send_response(status)
        handler.send_header('content-type', 'application/json; charset=utf-8')
        handler.send_header('content-length', str(len(body)))
        handler.send_header('connection', 'close')
        handler.end_headers(); handler.wfile.write(body)
        handler.close_connection = True
        if status in (401, 404):
            net.finish_rejected_post(handler)

    def _read_body(self, handler) -> bytes | None:
        if 'chunked' in (handler.headers.get('transfer-encoding') or '').lower():
            chunks, size = [], 0
            while True:
                line = handler.rfile.readline(1024)
                length = int(line.split(b';')[0].strip() or b'0', 16)
                if length == 0:
                    while handler.rfile.readline(1024) not in (b'\r\n', b'\n', b''): pass
                    return b''.join(chunks)
                size += length
                if size > MAX_BODY: return None
                chunks.append(handler.rfile.read(length)); handler.rfile.readline(1024)
        length = int(handler.headers.get('content-length') or 0)
        return None if length > MAX_BODY else handler.rfile.read(length)

    def _handle(self, handler):
        path = handler.path
        scheme = self._authorized(handler.headers)
        if not scheme:
            return self._reply(handler, 401, _error('办事中转：令牌不对，拒绝转发'))
        if not (path == self.prefix or path.startswith(self.prefix + '/') or path.startswith(self.prefix + '?')) or '..' in path.split('?')[0]:
            return self._reply(handler, 404, _error('办事中转：只转发账户地址下的接口'))
        try:
            body = self._read_body(handler) if handler.command == 'POST' else None
        except (ValueError, OSError):
            return self._reply(handler, 400, _error('办事中转：请求体格式不对'))
        if handler.command == 'POST' and body is None:
            return self._reply(handler, 413, _error('办事中转：请求体太大'))
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in _DROP_REQUEST}
        headers['accept-encoding'] = 'identity'
        if scheme == 'bearer': headers['authorization'] = f'Bearer {self._key}'
        else: headers['x-api-key'] = self._key
        request = Request(self.origin + path, data=body, headers=headers, method=handler.command)
        try:
            response = self._open(request, timeout=UPSTREAM_TIMEOUT)
        except HTTPError as error:
            if 300 <= error.code < 400:   # 不跟随，也不把 Location 交给 Harness：Key 只发往账户主机
                error.close()
                return self._reply(handler, 502, _error(f'模型服务返回跳转（HTTP {error.code}），已拦截，Key 不会跟随跳转；请检查账户地址'))
            response = error
        except (URLError, OSError) as error:
            return self._reply(handler, 502, _error(f'办事中转连不上模型服务：{getattr(error, "reason", error)}'))
        self._stream(handler, response)

    def _stream(self, handler, response):
        """状态码和安全的响应头原样转回，正文按块转（SSE 不能等整段读完）。"""
        try:
            handler.send_response(getattr(response, 'status', None) or response.getcode())
            for name, value in response.headers.items():
                if name.lower() not in _DROP_RESPONSE: handler.send_header(name, value)
            handler.send_header('transfer-encoding', 'chunked')
            handler.end_headers()
            read = getattr(response, 'read1', None) or response.read
            while True:
                chunk = read(65536)
                if not chunk: break
                handler.wfile.write(b'%x\r\n%s\r\n' % (len(chunk), chunk)); handler.wfile.flush()
            handler.wfile.write(b'0\r\n\r\n'); handler.wfile.flush()
        except (OSError, socket.timeout):
            handler.close_connection = True   # 任一方断开：放弃这次转发，Harness 按网络错误重试或报错
        finally:
            try: response.close()
            except Exception: pass
