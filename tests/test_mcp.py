"""真实回环 HTTP 协议测试；只用临时文件，不联网到模型、不读取私人配置。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from aicompanion.agent.mcp_server import McpToolServer, dependency_problem, tool_client
from aicompanion.agent.tools_server import Task, ToolBox


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inside = self.root/'inside'; self.inside.mkdir()
        self.outside = self.root/'outside'; self.outside.mkdir()
        self.box = ToolBox(trash=self.root/'trash')
        self.box.task = Task(allowed=[self.inside.resolve()])
        self.server = McpToolServer(self.box)

    def tearDown(self):
        self.server.close()
        self.temp.cleanup()

    def post(self, body, headers=None):
        raw = json.dumps(body).encode('utf-8') if isinstance(body, dict) else body
        fields = {'Authorization': 'Bearer '+self.server.token, 'Content-Type': 'application/json',
                  'Accept': 'application/json, text/event-stream'}
        fields.update(headers or {})
        req = urllib.request.Request(self.server.url, data=raw, headers=fields)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as response:
            return json.load(response)

    def test_lazy_optional_dependency_and_close_without_start(self):
        self.assertIsNone(self.server._thread)
        with patch('aicompanion.agent.mcp_server.importlib.util.find_spec', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'MCP SDK'):
                _ = self.server.url
        self.assertIsNone(self.server._thread)
        self.server.close(); self.server.close()
        with self.assertRaisesRegex(RuntimeError, '关闭'):
            _ = self.server.url

    def test_concurrent_start_is_single_server_and_close_releases_port(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            urls = list(pool.map(lambda _: self.server.url, range(4)))
        self.assertEqual(len(set(urls)), 1)
        self.assertEqual(self.server._socket.getsockname()[0], '127.0.0.1')
        address = self.server._socket.getsockname()
        self.assertEqual(self.server.acp_entry()['url'], urls[0])
        self.server.close(); self.server.close()
        self.assertFalse(self.server._thread.is_alive())
        with socket.socket() as probe:
            probe.settimeout(1)
            self.assertNotEqual(probe.connect_ex(address), 0)

    def test_partial_dependencies_are_reported_before_start(self):
        for missing in ('uvicorn', 'httpx2', 'anyio'):
            with self.subTest(missing=missing), patch(
                    'aicompanion.agent.mcp_server.importlib.util.find_spec',
                    side_effect=lambda name: None if name == missing else object()):
                self.assertIn(missing, dependency_problem())
                with self.assertRaisesRegex(RuntimeError, missing):
                    _ = self.server.url
                self.assertIsNone(self.server._thread)

    def test_shutdown_timeout_does_not_abort_application_shutdown(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from aicompanion.ui.agent_session import AgentSession
        server = McpToolServer(self.box)
        thread = Mock()
        thread.is_alive.return_value = True
        server._thread = thread
        session = SimpleNamespace(box=self.box, server=server, dsh=Mock())
        with self.assertLogs('aicompanion.agent.mcp_server', level='WARNING'):
            AgentSession.shutdown(session)
        self.assertTrue(self.box.task.cancelled.is_set())
        session.dsh.close.assert_called_once()
        self.assertIn('等待期限', server.shutdown_problem)
        self.assertIs(server._thread, thread)
        thread.is_alive.return_value = False
        self.assertTrue(server.close())
        self.assertEqual(server.shutdown_problem, '')

    def test_closed_loop_during_shutdown_is_harmless(self):
        from unittest.mock import Mock
        _ = self.server.url
        loop = Mock()
        loop.is_running.return_value = True
        loop.call_soon_threadsafe.side_effect = RuntimeError('loop closed')
        # 真循环在调用前退出；只模拟检查与通知之间的关闭竞态。
        actual = self.server._loop
        actual.call_soon_threadsafe(setattr, self.server._server, 'should_exit', True)
        with patch.object(self.server, '_loop', loop):
            self.assertTrue(self.server.close())

    def test_startup_failure_is_reported_and_thread_finishes(self):
        with patch.object(self.server, '_register', side_effect=ValueError('registration failed')):
            with self.assertRaisesRegex(RuntimeError, 'registration failed'):
                _ = self.server.url
        self.server.close()
        self.assertFalse(self.server._thread.is_alive())

    def test_auth_origin_and_invalid_json(self):
        for auth in ('', 'Bearer wrong'):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'Authorization': auth})
            self.assertEqual(raised.exception.code, 401)
            raised.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'Origin': 'https://untrusted.example'})
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.post(b'{invalid')
        self.assertEqual(raised.exception.code, 400)
        raised.exception.close()

    def test_protocol_versions_and_schema_validate_before_dispatch(self):
        records = []; self.box.on_call = records.append
        async def check():
            for mode, version in [('legacy', '2025-11-25'), ('auto', '2026-07-28')]:
                async with tool_client(self.server, mode=mode) as client:
                    self.assertEqual(client.protocol_version, version)
                    listed = (await client.list_tools()).tools
                    self.assertEqual({tool.name for tool in listed}, {tool['name'] for tool in ToolBox.TOOLS})
                    schemas = {tool.name: tool.input_schema for tool in listed}
                    self.assertEqual(set(schemas['edit_file']['required']), {'path', 'old_text', 'new_text'})
                    result = await client.call_tool('write_file', {'path': str(self.inside/'bad.txt'), 'content': []})
                    self.assertTrue(result.is_error)
                    self.assertFalse((self.inside/'bad.txt').exists())
        asyncio.run(check())
        self.assertEqual(records, [])
        for version in ('2025-03-26', '2025-06-18', '2025-11-25'):
            reply = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                               'params': {'protocolVersion': version, 'capabilities': {},
                                          'clientInfo': {'name': 'legacy-probe', 'version': '1'}}})
            self.assertEqual(reply['result']['protocolVersion'], version)
        reply = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize',
                           'params': {'protocolVersion': 'not-implemented', 'capabilities': {},
                                      'clientInfo': {'name': 'probe', 'version': '1'}}})
        self.assertNotEqual(reply.get('result', {}).get('protocolVersion'), 'not-implemented')

    def test_gate_failure_is_tool_error_and_approval_does_not_block_read(self):
        entered = threading.Event(); release = threading.Event()
        def approve(*args):
            entered.set(); release.wait(3); return 'no'
        self.box.approve = approve
        (self.inside/'read.txt').write_text('available', encoding='utf-8')
        async def check():
            async with tool_client(self.server, mode='legacy') as client:
                waiting = asyncio.create_task(client.call_tool('write_file',
                    {'path': str(self.outside/'blocked.txt'), 'content': 'blocked'}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    start = time.monotonic()
                    reply = await asyncio.wait_for(client.call_tool('read_file',
                        {'path': str(self.inside/'read.txt')}), timeout=1)
                    self.assertLess(time.monotonic()-start, 1)
                    self.assertFalse(reply.is_error)
                    self.assertIn('available', reply.content[0].text)
                    self.assertFalse(waiting.done())
                finally:
                    release.set()
                denial = await waiting
                self.assertTrue(denial.is_error)
                self.assertIn('被拒绝', denial.content[0].text)
        try:
            asyncio.run(check())
        finally:
            release.set()
        self.assertFalse((self.outside/'blocked.txt').exists())

    def test_pending_ui_approval_observes_original_task_cancellation(self):
        from types import SimpleNamespace
        from aicompanion.ui.agent_session import AgentSession
        original = self.box.task
        def ask(spec, box):
            original.cancelled.set()
            self.box.task = Task(run_id=2, allowed=[self.inside])
        notices = []
        session = SimpleNamespace(box=self.box, bridge=SimpleNamespace(
            ask=SimpleNamespace(emit=ask), event=SimpleNamespace(emit=lambda *args: notices.append(args))))
        started = time.monotonic()
        self.assertEqual(AgentSession._wait_ui(session, {}), 'no')
        self.assertLess(time.monotonic()-started, .5)
        self.assertFalse(self.box.task.cancelled.is_set())
        self.assertEqual(notices, [])

    def test_ui_approval_uses_thread_bound_task_before_emitting(self):
        from types import SimpleNamespace
        from aicompanion.ui.agent_session import AgentSession
        original = Task(run_id=1, allowed=[self.inside])
        current = Task(run_id=2, allowed=[self.inside])
        original.cancelled.set()
        session = SimpleNamespace(box=SimpleNamespace(current=original, task=current),
            bridge=SimpleNamespace(ask=SimpleNamespace(emit=lambda *args: None),
                                   event=SimpleNamespace(emit=lambda *args: None)))
        started = time.monotonic()
        self.assertEqual(AgentSession._wait_ui(session, {}), 'no')
        self.assertLess(time.monotonic()-started, .5)
        self.assertFalse(current.cancelled.is_set())

    def test_mcp_approval_keeps_cancelled_task_when_next_task_starts(self):
        """SDK 工作线程中的旧审批不能在下一任务启动后获得新的授权。"""
        from types import SimpleNamespace
        from aicompanion.ui.agent_session import AgentSession
        entered = threading.Event()
        original = self.box.task
        seen = []
        def ask(spec, answer):
            seen.append(self.box.current)
            entered.set()
        session = SimpleNamespace(box=self.box, bridge=SimpleNamespace(
            ask=SimpleNamespace(emit=ask), event=SimpleNamespace(emit=lambda *args: None)))
        self.box.approve = lambda *args: AgentSession._wait_ui(session, {})
        target = self.outside/'old-task.txt'
        async def check():
            async with tool_client(self.server, mode='legacy') as client:
                pending = asyncio.create_task(client.call_tool('write_file',
                    {'path': str(target), 'content': 'must not be written'}))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    # 新任务不取消；只有原任务被取消，确保检查没有误用 box.task。
                    self.box.task = Task(run_id=2, allowed=[self.inside])
                    original.cancelled.set()
                    result = await asyncio.wait_for(pending, timeout=2)
                    self.assertTrue(result.is_error)
                    self.assertFalse(self.box.task.cancelled.is_set())
                    current = await client.call_tool('list_dir', {'path': str(self.inside)})
                    self.assertFalse(current.is_error)
                finally:
                    original.cancelled.set()
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
        asyncio.run(check())
        self.assertEqual(seen, [original])
        self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
