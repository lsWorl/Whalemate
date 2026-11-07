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

from aicompanion.agent.mcp_server import McpToolServer, tool_client
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


if __name__ == '__main__':
    unittest.main()
