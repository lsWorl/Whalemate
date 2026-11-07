"""干活：DSH（ACP）后端用假运行时测，工具把关用临时文件夹测。不联网、不需要真的 DSH。"""
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest

from aicompanion.agent import dsh_backend
from aicompanion.agent.dsh_backend import DshAgent, pick_workspace
from aicompanion.agent.tools_server import Task, ToolBox, is_read_only_command
from aicompanion.agent.mcp_server import McpToolServer, tool_client


class FakeRuntime:
    """假 dsh 运行时：收到 session/prompt 时按脚本发 session/update，再回 stopReason。"""

    def __init__(self, script, stop_reason='end_turn', hang=False):
        self.script = script; self.stop_reason = stop_reason; self.hang = hang
        self.out = queue.Queue(); self.sent = []; self.alive = True; self.stdin = self; self.stdout = self; self.stderr = None
        self.pending_prompt = None

    # stdin
    def write(self, data):
        message = json.loads(data); self.sent.append(message)
        method = message.get('method'); rid = message.get('id')
        if method == 'initialize': self._reply(rid, {'protocolVersion': 1})
        elif method == 'session/new': self._reply(rid, {'sessionId': 's1', 'configOptions': [{'id': 'model', 'currentValue': '["deepseek-official","deepseek-v4-flash"]'}]})
        elif method == 'session/set_config_option': self._reply(rid, {})
        elif method == 'session/prompt':
            for update in self.script:
                self.out.put(json.dumps({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': 's1', 'update': update}})+'\n')
            if self.hang: self.pending_prompt = rid
            else: self._reply(rid, {'stopReason': self.stop_reason})
        elif method == 'session/cancel' and self.pending_prompt is not None:
            self._reply(self.pending_prompt, {'stopReason': 'cancelled'}); self.pending_prompt = None

    def flush(self): pass

    def _reply(self, rid, result):
        self.out.put(json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result})+'\n')

    # stdout
    def __iter__(self): return self

    def __next__(self):
        line = self.out.get()
        if line is None: raise StopIteration
        return line

    def poll(self): return None if self.alive else 0

    def terminate(self): self.alive = False; self.out.put(None)

    def wait(self, timeout=None): return 0

    def kill(self): self.terminate()

    def close(self): pass


SCRIPT = [
    {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': '我先看看文件夹。'}},
    {'sessionUpdate': 'tool_call', 'toolCallId': 'c1', 'title': 'mcp__aicompanion__list_dir', 'rawInput': {'path': r'D:\x'}},
    {'sessionUpdate': 'tool_call_update', 'toolCallId': 'c1', 'status': 'completed'},
    {'sessionUpdate': 'usage_update', 'used': 1234, 'size': 1000000},
    {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': '本鲸'}},
    {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': '做完啦。'}},
]


class AcpBackendTest(unittest.TestCase):
    def setUp(self):
        self.original = DshAgent._usage
        DshAgent._usage = staticmethod(lambda sid: dsh_backend.Usage(1000, 50, 800))

    def tearDown(self):
        DshAgent._usage = self.original

    def test_events_steps_usage(self):
        runtime = FakeRuntime(SCRIPT); agent = DshAgent(launcher=lambda env: runtime); events = []
        agent.mcp_servers = [{'type': 'http', 'name': 'aicompanion', 'url': 'http://127.0.0.1:1/mcp', 'headers': []}]
        result = agent.run('做事', Path('.'), 'deepseek-v4-pro', 'key', '', lambda k, d: events.append((k, d)))
        self.assertEqual(result.status, 'done'); self.assertEqual(result.text, '本鲸做完啦。')
        self.assertEqual([s[0] for s in result.steps], ['list_dir'])
        self.assertEqual((result.usage.prompt, result.usage.completion, result.usage.cached), (1000, 50, 800))
        self.assertIn(('usage', {'prompt': 1000, 'completion': 50, 'cached': 800, 'estimated': False}), events)
        new = next(m for m in runtime.sent if m.get('method') == 'session/new')
        self.assertEqual(new['params']['mcpServers'][0]['name'], 'aicompanion')
        option = next(m for m in runtime.sent if m.get('method') == 'session/set_config_option')
        self.assertIn('deepseek-v4-pro', option['params']['value'])
        agent.close()

    def test_stop_sends_cancel(self):
        runtime = FakeRuntime(SCRIPT[:1], hang=True); agent = DshAgent(launcher=lambda env: runtime)
        threading.Timer(0.3, agent.stop).start()
        result = agent.run('长任务', Path('.'))
        self.assertEqual(result.status, 'cancelled')
        self.assertTrue(any(m.get('method') == 'session/cancel' for m in runtime.sent))
        agent.close()

    def test_runtime_exit_is_failure(self):
        runtime = FakeRuntime([], hang=True); agent = DshAgent(launcher=lambda env: runtime)
        threading.Timer(0.3, runtime.terminate).start()
        result = agent.run('任务', Path('.'))
        self.assertEqual(result.status, 'failed')

    def test_limit_status(self):
        agent = DshAgent(launcher=lambda env: FakeRuntime([], stop_reason='max_tokens'))
        self.assertEqual(agent.run('x', Path('.')).status, 'limit'); agent.close()

    def test_patch_disables_native_tools(self):
        for row in ('tool-pwsh', 'tool-fs', 'tool-fs-search', 'tool-bash', 'tool-workflow'):
            self.assertIn(f'- id: {row}\n  disabled: true', dsh_backend.PATCH)
        self.assertIn('session-log-deepseek', dsh_backend.PATCH)

    def test_base_url_and_problem(self):
        self.assertIsNone(dsh_backend.dsh_base_url('https://api.deepseek.com'))
        self.assertEqual(dsh_backend.dsh_base_url('https://proxy.example/anthropic'), 'https://proxy.example/anthropic')
        self.assertIsInstance(dsh_backend.problem(), str)

    def test_pick_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(pick_workspace('随便聊聊', folder), Path(folder))


class ToolBoxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.inside = root/'ws'; self.inside.mkdir(); self.outside = root/'other'; self.outside.mkdir()
        self.secret = root/'project'/'.env'; self.secret.parent.mkdir(); self.secret.write_text('KEY=1')
        self.asked = []; self.answer = 'no'
        self.box = ToolBox(secrets=[self.secret], trash=root/'trash', run_command=lambda command, folder: f'ran {command}')
        self.box.approve = lambda kind, title, detail: (self.asked.append(kind), self.answer)[1]
        self.records = []; self.box.on_call = self.records.append
        self.box.task = Task(run_id=7, allowed=[self.inside.resolve()])

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, **args):
        return self.box.call(name, args)

    def test_write_inside_is_free_and_backs_up(self):
        target = self.inside/'a.txt'
        self.assertFalse(self.call('write_file', path=str(target), content='one')[1])
        text, error = self.call('write_file', path=str(target), content='two')
        self.assertFalse(error); self.assertIn('留底', text); self.assertEqual(target.read_text(), 'two')
        self.assertEqual(self.asked, [])
        backups = list((Path(self.tmp.name)/'trash').rglob('a.txt'))
        self.assertEqual(backups[0].read_text(), 'one')

    def test_write_outside_asks_and_respects_no(self):
        target = self.outside/'b.txt'
        text, error = self.call('write_file', path=str(target), content='x')
        self.assertTrue(error); self.assertIn('被拒绝', text); self.assertFalse(target.exists())
        self.assertEqual(self.asked, ['write']); self.assertEqual(self.records[-1]['decision'], 'denied')
        self.answer = 'always'
        self.assertFalse(self.call('write_file', path=str(target), content='x')[1])
        self.assertFalse(self.call('write_file', path=str(self.outside/'c.txt'), content='y')[1])   # 同一个文件夹不再问
        self.assertEqual(self.asked, ['write', 'write'])

    def test_delete_always_asks_and_goes_to_trash(self):
        target = self.inside/'old.txt'; target.write_text('keep me')
        self.assertTrue(self.call('delete_path', path=str(target))[1]); self.assertTrue(target.exists())
        self.answer = 'once'
        text, error = self.call('delete_path', path=str(target))
        self.assertFalse(error); self.assertFalse(target.exists())
        self.assertEqual([p.read_text() for p in (Path(self.tmp.name)/'trash').rglob('old.txt')], ['keep me'])
        self.assertEqual(self.asked, ['delete', 'delete'])

    def test_delete_own_new_file_is_free(self):
        target = self.inside/'tmp.txt'
        self.call('write_file', path=str(target), content='mine')
        self.assertFalse(self.call('delete_path', path=str(target))[1]); self.assertEqual(self.asked, [])

    def test_secrets_never_readable(self):
        for name, args in (('read_file', {'path': str(self.secret)}), ('run_powershell', {'command': 'Get-Content .env'}),
                           ('run_powershell', {'command': 'Get-Content ..\\project\\.env | Select-Object -First 1'})):
            text, error = self.box.call(name, args)
            self.assertTrue(error, name); self.assertNotIn('KEY=1', text)
        text, _ = self.call('grep', pattern='KEY', path=str(self.secret.parent.parent))   # 搜索时跳过私密文件
        self.assertNotIn('KEY=1', text)
        self.assertEqual(self.asked, [])

    def test_edit_requires_unique_match(self):
        target = self.inside/'e.txt'; target.write_text('a b a')
        self.assertTrue(self.call('edit_file', path=str(target), old_text='a', new_text='z')[1])
        self.assertFalse(self.call('edit_file', path=str(target), old_text='b', new_text='c')[1])
        self.assertEqual(target.read_text(), 'a c a')

    def test_commands(self):
        self.assertTrue(is_read_only_command('Get-ChildItem D:\\x | Measure-Object'))
        self.assertTrue(is_read_only_command('git status; git log -3'))
        self.assertFalse(is_read_only_command('Remove-Item D:\\x -Recurse'))
        self.assertFalse(is_read_only_command('Get-ChildItem > out.txt'))
        self.assertFalse(is_read_only_command('npm install'))
        self.assertFalse(is_read_only_command('& rm x'))
        self.assertFalse(is_read_only_command('"x: $(Remove-Item a)"'))
        self.assertFalse(is_read_only_command('Get-ChildItem | Where-Object { Remove-Item $_ }'))
        self.assertFalse(is_read_only_command("[IO.File]::WriteAllText('a','b')"))
        self.assertTrue(is_read_only_command("$p='D:\\x'; $f=Get-ChildItem $p -File; \"共 \" + $f.Count; (Get-ChildItem $p -Recurse).Count"))
        self.assertTrue(is_read_only_command("if (Test-Path x) { 'yes' } else { 'no' }"))
        self.assertTrue(is_read_only_command("$p = 'D:\\x'; (Get-ChildItem -LiteralPath $p -File | Measure-Object).Count"))
        self.assertFalse(is_read_only_command('Get-ChildItem | ForEach-Object { Remove-Item $_ }'))
        self.assertEqual(self.call('run_powershell', command='Get-Date')[0], 'ran Get-Date'); self.assertEqual(self.asked, [])
        self.assertTrue(self.call('run_powershell', command='Remove-Item x')[1]); self.assertEqual(self.asked, ['command'])

    def test_cancelled_task_refuses(self):
        self.box.task.cancelled.set()
        self.assertTrue(self.call('list_dir', path=str(self.inside))[1])

    def test_mcp_server_round_trip(self):
        import asyncio
        server = McpToolServer(self.box)
        async def check():
            async with tool_client(server, mode='legacy') as client:
                self.assertEqual(client.server_info.name, 'aicompanion')
                self.assertEqual(len((await client.list_tools()).tools), 10)
                (self.inside/'r.txt').write_text('hello')
                reply = await client.call_tool('read_file', {'path': str(self.inside/'r.txt')})
                self.assertFalse(reply.is_error)
                self.assertIn('hello', reply.content[0].text)
            token, server.token = server.token, 'wrong'
            try:
                with self.assertRaises(Exception):
                    async with tool_client(server):
                        pass
            finally:
                server.token = token
        try:
            asyncio.run(check())
        finally:
            server.close()


if __name__ == '__main__':
    unittest.main()
