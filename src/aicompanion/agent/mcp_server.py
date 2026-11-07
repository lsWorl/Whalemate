"""官方 MCP 协议适配；工具规则仍由 ToolBox 执行。

服务器按需启动，避免只使用聊天/桌宠的安装也加载可选的 Agent 依赖。
HTTP 只绑定回环地址，随机令牌不进入日志；SDK 负责版本、输入校验和传输。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import logging
import os
import secrets
import socket
import threading

from aicompanion.agent.tools_server import ToolBox

LOGGER = logging.getLogger(__name__)


def dependency_problem() -> str:
    missing = [name for name in ('mcp', 'uvicorn', 'httpx2', 'anyio')
               if importlib.util.find_spec(name) is None]
    if missing:
        return '干活所需依赖未安装（官方 MCP SDK 等）：'+ '、'.join(missing)+'。请安装 .[agent] 或运行 tools\\setup_dsh.bat。'
    return ''


class _BearerAuth:
    """本机父子进程之间的随机令牌认证；生命周期事件交给原 ASGI 应用。"""

    def __init__(self, app, token):
        self.app, self.expected = app, ('Bearer '+token).encode('ascii')

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            supplied = [value for key, value in scope.get('headers', []) if key.lower() == b'authorization']
            if len(supplied) != 1 or not secrets.compare_digest(supplied[0], self.expected):
                await send({'type': 'http.response.start', 'status': 401,
                            'headers': [(b'content-type', b'text/plain'), (b'www-authenticate', b'Bearer')]})
                await send({'type': 'http.response.body', 'body': b'Unauthorized'})
                return
        await self.app(scope, receive, send)


class McpToolServer:
    """保留原服务的 url / token / acp_entry / close 接口，后台线程运行 ASGI。"""

    def __init__(self, box: ToolBox):
        self.box = box
        self.token = os.urandom(32).hex()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = None
        self._loop = None
        self._server = None
        self._socket = None
        self._address = None
        self._error = None
        self._closed = False
        self.shutdown_problem = ''

    @property
    def url(self) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError('MCP 工具服务已关闭。')
            if self._thread is None:
                missing = dependency_problem()
                if missing:
                    raise RuntimeError(missing)
                self._thread = threading.Thread(target=self._run, name='aicompanion-mcp', daemon=True)
                self._thread.start()
        if not self._ready.wait(15):
            self.close()
            raise TimeoutError('MCP 工具服务启动超时。')
        if self._error:
            raise RuntimeError('MCP 工具服务启动失败：'+str(self._error)) from self._error
        if self._closed or self._address is None:
            raise RuntimeError('MCP 工具服务已关闭。')
        return self._address

    def acp_entry(self) -> dict:
        return {'type': 'http', 'name': 'aicompanion', 'url': self.url,
                'headers': [{'name': 'Authorization', 'value': 'Bearer '+self.token}]}

    def _register(self, mcp):
        import anyio
        from mcp.types import CallToolResult, TextContent

        async def call(name, **args):
            # 确认气泡会等待 UI 回答；不占用 HTTP 事件循环。
            # HTTP 请求取消只放弃等待，不会杀死 Python 工作线程；实际停止仍由所属 Task.cancelled 把关。
            text, error = await anyio.to_thread.run_sync(
                self.box.call, name, args, abandon_on_cancel=True)
            return CallToolResult(content=[TextContent(text=text)], is_error=error)

        async def list_dir(path: str):
            return await call('list_dir', path=path)

        async def glob(pattern: str, path: str):
            return await call('glob', pattern=pattern, path=path)

        async def grep(pattern: str, path: str, glob: str | None = None):
            return await call('grep', pattern=pattern, path=path, glob=glob)

        async def read_file(path: str, offset: int = 1, limit: int | None = None):
            return await call('read_file', path=path, offset=offset, limit=limit)

        async def write_file(path: str, content: str):
            return await call('write_file', path=path, content=content)

        async def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False):
            return await call('edit_file', path=path, old_text=old_text, new_text=new_text, replace_all=replace_all)

        async def make_dir(path: str):
            return await call('make_dir', path=path)

        async def move_path(source: str, destination: str):
            return await call('move_path', source=source, destination=destination)

        async def delete_path(path: str, reason: str = ''):
            return await call('delete_path', path=path, reason=reason)

        async def run_powershell(command: str, cwd: str | None = None, reason: str = ''):
            return await call('run_powershell', command=command, cwd=cwd, reason=reason)

        descriptions = {tool['name']: tool['description'] for tool in ToolBox.TOOLS}
        for function in (list_dir, glob, grep, read_file, write_file, edit_file, make_dir, move_path,
                         delete_path, run_powershell):
            mcp.tool(description=descriptions[function.__name__], structured_output=False)(function)

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as error:
            self._error = error
        finally:
            if self._socket is not None:
                self._socket.close()
            self._ready.set()

    async def _serve(self):
        import uvicorn
        from mcp.server import MCPServer
        from mcp.server.transport_security import TransportSecuritySettings

        self._loop = asyncio.get_running_loop()
        sock = self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        self._address = f'http://127.0.0.1:{port}/mcp'
        mcp = MCPServer('aicompanion', version='1', log_level='ERROR')
        self._register(mcp)
        app = mcp.streamable_http_app(
            json_response=True, stateless_http=True, host='127.0.0.1',
            transport_security=TransportSecuritySettings(
                allowed_hosts=[f'127.0.0.1:{port}'], allowed_origins=[f'http://127.0.0.1:{port}']))
        self._server = uvicorn.Server(uvicorn.Config(
            _BearerAuth(app, self.token), host='127.0.0.1', port=port,
            log_config=None, log_level='error', access_log=False, proxy_headers=False,
            lifespan='on', ws='none', timeout_graceful_shutdown=2, limit_concurrency=32))
        serving = asyncio.create_task(self._server.serve(sockets=[sock]))
        while not self._server.started:
            if serving.done():
                await serving
                raise RuntimeError('HTTP 服务未能就绪。')
            await asyncio.sleep(.01)
        self._ready.set()
        if self._closed:
            self._server.should_exit = True
        await serving

    def close(self, timeout: float = 8) -> bool:
        """请求退出并有界等待；超时保留线程引用，记录状态，不能中断应用的退出流程。"""
        with self._lock:
            self._closed = True
            thread, server, loop = self._thread, self._server, self._loop
        if thread is None:
            return True
        self.box.task.cancelled.set()
        if server is not None and loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(setattr, server, 'should_exit', True)
            except RuntimeError:
                pass   # 检查之后循环可能已经关闭；继续检查线程实际是否退出。
        if thread is not threading.current_thread():
            thread.join(timeout=max(0, timeout))
            if thread.is_alive():
                self.shutdown_problem = 'MCP 服务未在等待期限内退出，已请求停止；后台线程仍在清理。'
                LOGGER.warning(self.shutdown_problem)
                return False
        self.shutdown_problem = ''
        return True


@asynccontextmanager
async def tool_client(server: McpToolServer, *, mode='auto'):
    """离线自检使用官方客户端；回环请求不读取系统代理或携带其他服务凭据。"""
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer '+server.token}, trust_env=False, timeout=15) as http:
        async with Client(streamable_http_client(server.url, http_client=http), mode=mode) as client:
            yield client
