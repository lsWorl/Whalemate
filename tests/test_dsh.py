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
        self.assertFalse(is_read_only_command('git status; git log -3 -p'))
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

    def test_move_checks_source_and_target(self):
        own = self.inside/'mine.txt'
        self.call('write_file', path=str(own), content='x')                       # 她这次新建的文件
        self.assertTrue(self.call('move_path', source=str(own), destination=str(self.outside/'mine.txt'))[1])
        self.assertEqual(self.asked, ['move']); self.assertTrue(own.exists())    # 挪到外面也要问，拒绝后原地不动
        self.assertFalse(self.call('move_path', source=str(own), destination=str(self.inside/'sub'/'m.txt'))[1])
        self.assertEqual(self.asked, ['move'])                                    # 放开的文件夹里随便挪
        foreign = self.outside/'theirs.txt'; foreign.write_text('t')
        self.assertTrue(self.call('move_path', source=str(foreign), destination=str(self.inside/'t.txt'))[1])
        self.assertEqual(self.asked, ['move', 'move']); self.assertTrue(foreign.exists())

    def test_read_only_bypasses(self):
        for command in ('& $c x', "$c='Remove-Item'; & $c a", '.("Remove-Item") x', "& 'Remove-Item' x", "iex 'rm x'",
                        'git branch -D old', 'git remote add o u', 'git diff --no-ext-diff --no-textconv --output=x.txt', 'Get-Content a | Tee-Object b',
                        'npx hexo server', 'Start-Process notepad', 'mkdir x'):
            self.assertFalse(is_read_only_command(command), command)
        for command in ('Get-ChildItem | Format-Table Name', 'Get-ChildItem D:\\del\\x', 'git branch -a', 'git remote -v', '& Get-Date'):
            self.assertTrue(is_read_only_command(command), command)

    def test_git_external_programs_need_approval(self):
        """Codex 复现的绕过：设环境变量 GIT_EXTERNAL_DIFF 后 git diff --ext-diff 被当作只读执行。"""
        for command in ("$env:GIT_EXTERNAL_DIFF = 'python probe.py'; git diff --no-index --ext-diff a.txt b.txt",
                        "${env:GIT_EXTERNAL_DIFF}='x'; git status", 'git diff a.txt', 'git show HEAD', 'git log -p', 'git log --stat',
                        'git log -S secret', 'git blame a.txt', 'git -c core.pager=evil status', 'git --exec-path=x status',
                        'git diff --no-ext-diff --no-textconv --ext-diff a b', 'git log --format=%G?', '$function:x = {1}'):
            self.assertFalse(is_read_only_command(command), command)
        for command in ('git diff --no-ext-diff --no-textconv a.txt', 'git show --no-ext-diff --no-textconv HEAD --stat',
                        'git log --oneline -5', 'git log --format=%h%x09%s --since=2026-01-01', 'git blame --no-textconv a.txt',
                        "$x = 'a'; git status", "$script:n = 1; git ls-files"):
            self.assertTrue(is_read_only_command(command), command)
        ran = []
        box = ToolBox(trash=Path(self.tmp.name)/'trash', run_command=lambda command, folder: ran.append(command) or 'ran')
        box.approve = lambda kind, title, detail: (self.asked.append(kind), 'no')[1]
        box.task = Task(run_id=8, allowed=[self.inside.resolve()])
        repro = "$env:GIT_EXTERNAL_DIFF = 'python probe.py'; git diff --no-index --ext-diff a.txt b.txt"
        text, error = box.call('run_powershell', {'command': repro, 'cwd': str(self.inside)})
        self.assertTrue(error); self.assertEqual(self.asked, ['command']); self.assertEqual(ran, [])   # 拒绝后根本没运行

    def test_git_safe_env_blocks_repo_configured_programs(self):
        """仓库配置里的 fsmonitor 会让 git status 执行程序：运行环境里的覆盖要关掉它；外部 diff 环境变量被清掉。"""
        import os, shutil, subprocess, sys
        from aicompanion.agent.tools_server import git_safe_env
        if not shutil.which('git'): self.skipTest('没有 git')
        repo = self.inside/'repo'; repo.mkdir(); marker = self.inside/'marker.txt'
        probe = self.inside/'probe.py'; probe.write_text(f'open({str(marker)!r}, "w").write("x")\n')
        run = lambda *a, env=None: subprocess.run(['git', *a], cwd=repo, env=env, capture_output=True, text=True)
        run('init', '-q'); (repo/'f.txt').write_text('a')
        run('config', 'core.fsmonitor', f'"{sys.executable}" "{probe}"')
        env = dict(os.environ, GIT_EXTERNAL_DIFF=f'"{sys.executable}" "{probe}"'); env.update(git_safe_env(env))
        self.assertNotIn('GIT_EXTERNAL_DIFF', env)
        self.assertEqual(run('status', '--short', env=env).returncode, 0)
        self.assertFalse(marker.exists())

    def test_git_option_abbreviations_and_expansions_need_approval(self):
        """Codex 复现：git 接受长选项缩写，--ext-di 在关闭开关之后重新打开外部 diff。名单外的一律转确认。"""
        for command in ('git diff --no-ext-diff --no-textconv --ext-di --no-index -- a.txt b.txt',
                        'git diff --no-ext-diff --no-textconv --ext a b', 'git diff --no-ext-diff --no-textconv --textc a',
                        'git show --no-ext-diff --no-textconv --ext-diff HEAD', 'git blame --no-textconv --textco a.txt',
                        'git log --pat', 'git log --oneline --ext-d', 'git status --porc --ext',
                        "$o = '--ext-diff'; git diff --no-ext-diff --no-textconv $o a b", 'git diff --no-ext-diff --no-textconv @o a b',
                        'git diff --no-ext-diff --no-textconv a,--ext-diff b', 'git diff --% --no-ext-diff --no-textconv --ext-diff',
                        'git diff --no-ext-diff --no-textconv "--ext-di" a', "git diff --no-ext-diff --no-textconv '--ext-di' a",
                        'git diff --no-ext-diff --no-textconv "$(\'--ext-diff\')" a', 'git log --format=%G?'):
            self.assertFalse(is_read_only_command(command), command)
        for command in ('git diff --no-ext-diff --no-textconv -- --ext-diff', 'git diff --no-textconv --no-ext-diff --stat HEAD~1',
                        "git diff --no-ext-diff --no-textconv -- 'a b.txt'", 'git status --short', 'git status -s -b',
                        'git show --no-ext-diff --no-textconv --stat HEAD', 'git log --oneline -5 -- src'):
            self.assertTrue(is_read_only_command(command), command)

    def test_allowed_git_diff_never_starts_repo_configured_program(self):
        """名单内的 diff / show / log 用真实 git 跑：仓库里配好的 diff.external 和 textconv 都不会被启动。"""
        import os, shlex, shutil, subprocess, sys
        from aicompanion.agent.tools_server import git_safe_env
        if not shutil.which('git'): self.skipTest('没有 git')
        repo = self.inside/'repo2'; repo.mkdir(); marker = self.inside/'marker2.txt'
        probe = self.inside/'probe2.py'; probe.write_text(f'open({str(marker)!r}, "w").write("x")\n')
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
        env.update(git_safe_env(env))
        git = lambda *a: subprocess.run(['git', *a], cwd=repo, env=env, capture_output=True, text=True)
        git('init', '-q'); (repo/'f.txt').write_text('a\n'); (repo/'.gitattributes').write_text('*.txt diff=probe\n')
        git('add', '.'); git('commit', '-qm', 'a'); (repo/'f.txt').write_text('b\n')
        program = f'"{sys.executable}" "{probe}"'.replace('\\', '/')
        git('config', 'diff.external', program); git('config', 'diff.probe.textconv', program)
        commands = ['git diff --no-ext-diff --no-textconv', 'git diff --no-textconv --no-ext-diff --stat',
                    'git show --no-ext-diff --no-textconv --stat HEAD', 'git log --oneline -1', 'git status --short']
        for command in commands:
            self.assertTrue(is_read_only_command(command), command)
            result = subprocess.run(shlex.split(command), cwd=repo, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, (command, result.stderr)); self.assertFalse(marker.exists(), command)
        # 对照：新版 git（Windows 实测）接受 --ext-di 缩写并重新打开外部 diff——上面的分类必须拦住它；旧版 git 直接报"无效选项"
        control = subprocess.run(['git', 'diff', '--no-ext-diff', '--no-textconv', '--ext-di'], cwd=repo, env=env, capture_output=True)
        if control.returncode == 0: self.assertTrue(marker.exists())

    def test_git_internals_are_not_free_to_write(self):
        config = self.inside/'.git'/'config'
        self.assertTrue(self.call('write_file', path=str(config), content='[core]\nfsmonitor = evil')[1])
        self.assertEqual(self.asked, ['write']); self.assertFalse(config.exists())
        hook = self.inside/'mine.txt'; self.call('write_file', path=str(hook), content='x')
        self.assertTrue(self.call('move_path', source=str(hook), destination=str(self.inside/'.git'/'hooks'/'pre-commit'))[1])
        self.assertEqual(self.asked, ['write', 'move'])
        self.assertFalse(self.call('write_file', path=str(self.inside/'.gitignore'), content='x')[1])   # .gitignore 不受影响

    def test_stop_kills_running_command(self):
        import sys, time
        box = ToolBox(trash=Path(self.tmp.name)/'trash'); box.task = Task(run_id=1, allowed=[self.inside])
        threading.Timer(0.5, box.task.cancelled.set).start()
        start = time.monotonic()
        text = box._run_process([sys.executable, '-c', 'import time; time.sleep(30)'], self.inside, dict(__import__('os').environ))
        self.assertLess(time.monotonic()-start, 5); self.assertIn('停', text)
        box.task = Task(run_id=2, allowed=[self.inside])
        text = box._run_process([sys.executable, '-c', 'import time; time.sleep(30)'], self.inside, dict(__import__('os').environ), timeout=0.5)
        self.assertIn('超过', text)
        self.assertIn('hello', box._run_process([sys.executable, '-c', 'print("hello")'], self.inside, dict(__import__('os').environ)))

    def test_old_command_stops_even_after_new_task_starts(self):
        """旧命令启动后：取消旧任务，并在它下一次轮询前换成新任务——旧命令仍要结束，新任务不受影响。"""
        from aicompanion.agent import tools_server
        box = ToolBox(trash=Path(self.tmp.name)/'trash'); old = Task(run_id=1, allowed=[self.inside]); box.task = old
        new = Task(run_id=2, allowed=[self.inside]); killed = []

        class FakeProc:
            pid = 4242; returncode = None
            def __init__(self, *a, **k):
                import io; self.stdout, self.stderr = io.BytesIO(b''), io.BytesIO(b''); self.polls = 0
            def poll(self):
                self.polls += 1
                if self.polls == 1: old.cancelled.set(); box.task = new      # 用户停了旧任务并马上开了新任务
                return None if not killed else -9
            def kill(self): pass
            def wait(self, timeout=None): return -9

        original_popen, original_kill = tools_server.subprocess.Popen, ToolBox.__dict__['_kill_tree']
        tools_server.subprocess.Popen = FakeProc; ToolBox._kill_tree = staticmethod(lambda proc: killed.append(proc.pid))
        try:
            box._local.task = old
            text = box._run_process(['x'], self.inside, {})
        finally:
            tools_server.subprocess.Popen, ToolBox._kill_tree = original_popen, original_kill
            box._local.task = None
        self.assertEqual(killed, [4242]); self.assertIn('停', text); self.assertFalse(new.cancelled.is_set())

    def test_late_record_keeps_original_run_id(self):
        records = []; self.box.on_call = records.append
        old = self.box.task
        def approve(kind, title, detail):
            self.box.task = Task(run_id=99, allowed=[self.inside])   # 等确认期间换了新任务
            return 'no'
        self.box.approve = approve
        self.call('delete_path', path=str(self.inside))
        self.assertEqual(records[-1]['run_id'], old.run_id)

    @unittest.skipIf(__import__('os').name == 'nt', '进程组用例在 POSIX 上跑；Windows 用 taskkill /T（见 Windows 实测）')
    def test_stop_kills_child_processes_too(self):
        import os, sys, time
        box = ToolBox(trash=Path(self.tmp.name)/'trash'); box.task = Task(run_id=1, allowed=[self.inside])
        pid_file = self.inside/'child.pid'
        script = (f"import subprocess,sys,time; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  f"open(r'{pid_file}','w').write(str(c.pid)); time.sleep(60)")
        threading.Timer(1.0, box.task.cancelled.set).start()
        box._run_process([sys.executable, '-c', script], self.inside, dict(os.environ))
        child = int(pid_file.read_text()); time.sleep(0.3)
        try: os.kill(child, 0); alive = True
        except OSError: alive = False
        if alive:   # 可能是僵尸进程：看状态
            alive = 'Z' not in Path(f'/proc/{child}/stat').read_text().split()[2] if os.path.exists(f'/proc/{child}/stat') else False
        self.assertFalse(alive)

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
