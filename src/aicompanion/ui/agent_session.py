"""把 DeepSeek Harness 接到桌宠：后台线程跑任务，进度用状态机和活动图标表现。

DSH 负责想和调度（还有网页搜索、分身）；读写文件、删除、命令由本进程的工具执行（agent/tools_server.py），
需要你同意的步骤变成确认气泡。这里负责：人设口吻、选工作区和放开的文件夹、确认气泡、进度、用量和额度、审计记录。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from aicompanion.agent.dsh_backend import DEFAULT_MODEL, DshAgent, pick_workspace, problem
from aicompanion.agent.paths import protected_reason, task_folders
from aicompanion.agent.tools_server import Task, ToolBox
from aicompanion.agent.mcp_server import McpToolServer, dependency_problem
from aicompanion.ui.chat_worker import TaskWorker
from aicompanion.usage import meter

APPROVAL_TIMEOUT = 120.0
TOKEN_BURN = 40_000   # 单次任务超过这么多词元，鲸娘会吐槽一句
TOOL_NAMES = {'list_dir': '看文件夹', 'glob': '找文件', 'grep': '搜文件内容', 'read_file': '读文件', 'write_file': '写文件',
              'edit_file': '改文件', 'make_dir': '建文件夹', 'move_path': '移动文件', 'delete_path': '删除', 'run_powershell': 'PowerShell',
              'web_search': '搜索网页', 'web_fetch': '读网页', 'subagent': '分身', 'subagent_fork': '分身', 'todo_write': '列待办', 'skill': '技能'}
OWN_TOOLS = {t['name'] for t in ToolBox.TOOLS}
ACTIVITY = {'web_search': 'searching', 'web_fetch': 'reading', 'read_file': 'reading', 'list_dir': 'reading', 'glob': 'searching',
            'grep': 'searching', 'write_file': 'writing', 'edit_file': 'writing', 'make_dir': 'writing', 'run_powershell': 'computer'}
CHOICES = {'write': ('这个文件夹本次都可以', True), 'command': ('本次的命令都可以', True), 'delete': (None, False), 'move': (None, False)}


def tool_label(name: str) -> str:
    return TOOL_NAMES.get(name, name)


class _Bridge(QObject):
    event = Signal(str, object)
    ask = Signal(object, object)          # (气泡内容, 回传盒子)


class AgentSession(QObject):
    finished = Signal(object)             # DshResult（附 run_id / tokens / cost）

    def __init__(self, window):
        super().__init__(window)
        self.window = window; self.worker = None; self.goal = ''
        self.dsh = DshAgent()
        self.box = ToolBox(secrets=self._secrets())
        self.box.approve = self._approve_tool; self.box.on_call = self._tool_record
        self.server = McpToolServer(self.box)
        self.run_state = {'run_id': 0, 'seq': 0}
        self.bridge = _Bridge(self)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(60_000, lambda: threading.Thread(target=self.housekeep, daemon=True, name='trash-clean').start())
        self.bridge.event.connect(self._event); self.bridge.ask.connect(self._ask)
        self.auto_approve = None   # 自检时替换成函数

    @property
    def busy(self) -> bool:
        return self.worker is not None

    # ---- 开始 / 停止 ---------------------------------------------------------------
    def start(self, goal: str, announce: bool = True) -> bool:
        w = self.window
        if self.busy: w.pet.show_bubble('我手上还有活没做完，等我一下～', 5); return False
        missing = problem() or dependency_problem()
        if missing: w.pet.show_bubble(missing, 8); w._status(missing); return False
        if w.settings.llm_provider == 'mock' or not getattr(w.settings, 'llm_api_key', ''):
            w.pet.show_bubble('离线模式下没法干活，需要先配置 DeepSeek 接口。', 8); return False
        if w.quota.check_silent('agent') == 'stop':
            w.pet.show_bubble('今天"干活"的词元额度用完了，明天再帮你做吧。', 8); return False
        database = w.database; character = w.controller.character
        self.goal = goal
        cwd = pick_workspace(goal, database.get_setting('dsh_workspace', ''))
        model = database.get_setting('dsh_model', DEFAULT_MODEL) or DEFAULT_MODEL
        allowed = self._allowed(goal, cwd)
        prompt = self._prompt(goal, cwd, allowed)
        run_id = database.add_agent_run(character.id, goal)
        state = self.run_state = {'run_id': run_id, 'seq': 0}
        self.box.task = Task(run_id=run_id, allowed=allowed)

        def on_event(kind, data):
            if kind == 'usage':
                with meter.using('agent', character.id, f'agent:{run_id}'):
                    meter.report(model, {'prompt_tokens': data['prompt'], 'completion_tokens': data['completion'],
                                         'prompt_cache_hit_tokens': data['cached']})
                if w.quota.check_silent('agent') == 'stop': self.dsh.stop()   # 额度用完立即停
            elif kind == 'tool' and data['tool'] not in OWN_TOOLS:
                # 自己的工具由 _tool_record 记（带审批结果）；DSH 自带的（网页、分身等）在这里记
                state['seq'] += 1
                step_id = database.add_agent_step(run_id, state['seq'], data['tool'], data['summary'][:4000], 'dsh', 'auto')
                database.finish_agent_step(step_id, data['summary'][:200])
            self.bridge.event.emit(kind, data)

        def run(token):
            try:
                self.dsh.mcp_servers = [self.server.acp_entry()]
            except Exception as error:
                database.finish_agent_run(run_id, 'failed', '', str(error)[:12000], 0, 0, 0.0)
                raise
            result = self.dsh.run(prompt, cwd, model, w.settings.llm_api_key, w.settings.llm_base_url, on_event)
            tokens, cost = self._usage(run_id)
            result.run_id, result.tokens, result.cost, result.cwd = run_id, tokens, cost, str(cwd)
            database.finish_agent_run(run_id, result.status, result.text[:2000], '' if result.status in ('done', 'cancelled') else (result.text[:500]+'\n'+result.raw)[:12000],
                                      state['seq'], tokens, cost)
            # 不走 TaskWorker 的 succeeded：任务被取消时它会吞掉结果，界面就一直停在"干活中"
            self.bridge.event.emit('result', result)
            self.housekeep()
            return result

        self.worker = TaskWorker(run, self)
        w.tasks.add(self.worker)
        self.worker.failed.connect(self._failed); self.got_result = False
        self.worker.finished.connect(self._worker_finished)
        w.pet.agent_busy = True
        w.pet.dispatch('work_started', tool='agent', task=goal); w.pet.show_activity('thinking')
        if announce: w._append_message('任务', goal)   # 从聊天里自动开始时，原话已经在聊天记录里了
        w._status(f'正在干活（工作区 {cwd}）')
        self.worker.start()
        return True

    def stop(self):
        if self.worker:
            self.box.task.cancelled.set(); self.dsh.stop()
        self.window.pet.close_confirm()

    def shutdown(self):
        self.box.task.cancelled.set(); self.dsh.close(); self.server.close()

    def housekeep(self):
        """清理回收区和 DSH 旧会话（后台线程）：按设置的保留天数和总量上限。"""
        try:
            from aicompanion.agent import housekeeping
            from aicompanion.agent.dsh_backend import dsh_home
            database = self.window.database
            report = housekeeping.clean(float(database.get_setting('trash_keep_days', housekeeping.DEFAULT_KEEP_DAYS) or 0),
                                        int(float(database.get_setting('trash_max_mb', 2048) or 2048)*1024*1024),
                                        [database.get_setting('blog_root', '')], dsh_home())
            database.set_setting('trash_last_clean', report.text())
            return report
        except Exception as error:
            return error

    # ---- 放开的文件夹、私密位置 -------------------------------------------------------------
    def _secrets(self):
        """她的工具碰不到的位置：项目的 .env、数据库所在文件夹、DSH 的会话记录、本应用的本机数据。"""
        from pathlib import Path
        import os
        from aicompanion.agent.sandbox import low_root
        w = self.window; project = Path(__file__).resolve().parents[3]
        items = [project/'.env', low_root(), Path(os.getenv('LOCALAPPDATA') or Path.home())/'AICompanion']
        db = getattr(w.settings, 'database_path', None)
        if db: items.append(Path(db).parent if Path(db).parent != project else Path(db))
        if getattr(getattr(w, 'database', None), 'database_path', None): items.append(Path(w.database.database_path))
        return items

    def _allowed(self, goal, cwd):
        """放开写的文件夹：原话里点名的（带扩展名的取所在文件夹）+ 工作区。包含私密数据或整个用户目录的不放开。"""
        from pathlib import Path
        out = []
        for folder in [*task_folders(goal), Path(cwd)]:
            try: folder = folder.resolve()
            except OSError: continue
            if not folder.is_dir() and folder.parent.is_dir(): folder = folder.parent
            if not folder.is_dir() or protected_reason(folder) or folder == Path.home().resolve(): continue
            if any(s == folder or folder in s.parents for s in self.box.secrets if s.exists()): continue
            if folder not in out: out.append(folder)
        return out

    def _usage(self, run_id):
        try:
            with self.window.database.transaction() as connection:
                row = connection.execute('SELECT SUM(prompt_tokens+completion_tokens) AS t, SUM(cost) AS c FROM token_usage WHERE ref=?',
                                         (f'agent:{run_id}',)).fetchone()
            return int(row['t'] or 0), float(row['c'] or 0)
        except Exception:
            return 0, 0.0

    def _prompt(self, goal: str, cwd, allowed=()) -> str:
        """交给 DSH 的输入：人设 + 环境说明 + 最近聊天 + 用户原话。"""
        w = self.window; character = w.controller.character; database = w.database
        blog = database.get_setting('blog_root', '')
        from pathlib import Path
        project = Path(__file__).resolve().parents[3]
        lines = [f'你是{character.name}。{(character.description or "")[:900]}',
                 f'说话方式：{(character.speaking_style or "")[:600]}',
                 '最后给用户的回复要保持上面的人设：一两句口语，像同事口头交代做了什么、结果在哪，不写报告、不列清单、不用 Markdown 标题，除非用户要细节。',
                 '做事要求：用户的电脑是 Windows。看文件、改文件、删文件、跑 PowerShell 都用 aicompanion 开头的那组工具；'
                 '尽量在后台完成，不要让用户手动操作；用户点名的路径、文件名、程序就用那个，不要擅自换；一句话里有几件事要按顺序全部做完。',
                 f'当前工作区：{cwd}。放开写的文件夹：{"；".join(str(a) for a in allowed) or "无"}——在这些里面写和改不用问；'
                 '在外面写、删除任何文件、运行会改东西的命令时，工具会自动弹窗问用户，你不用自己再问一遍。'
                 '如果工具返回"被拒绝"，就停下来如实告诉用户，不要换个办法绕过去，也不要教用户怎么绕过。密钥文件和我的数据库是读不到的，也别去找。']
        if blog: lines.append(f'用户的 Hexo 博客在 {blog}：草稿放 source/_drafts，本地预览用 `npx hexo server --draft -p 4000`（后台启动，别阻塞）。')
        if (project/'docs'/'implementation_status.md').exists():
            lines.append(f'AICompanion 项目在 {project}，写开发日志的素材在 docs/implementation_status.md。')
        history = self._history(goal)
        if history:
            lines.append('最近的聊天（只用来理解上下文）：\n'+'\n'.join(f'{"用户" if r["role"] == "user" else character.name}：{r["content"][:300]}' for r in history))
        lines.append(f'用户这次的原话：{goal}')
        return '\n\n'.join(lines)

    def _history(self, goal=''):
        """最近几句聊天作为背景。任务原话本身和她刚才那句答应不放进去。"""
        rows = [r for r in self.window.database.recent_messages(8, self.window.controller.character.id)
                if r['role'] in ('user', 'assistant') and r['content']]
        for k in range(len(rows)-1, -1, -1):
            if rows[k]['role'] == 'user' and rows[k]['content'].strip() == goal.strip():
                rows = rows[:k]; break
        return [{'role': r['role'], 'content': r['content']} for r in rows[-6:]]

    # ---- 审批与提问（DSH 的后台线程调用，阻塞等待界面回答） ---------------------------------------
    def _wait_ui(self, spec) -> str:
        from time import monotonic
        # T11 在工具线程绑定 current；兼容尚未合入 T11 的 ToolBox，不能把旧调用绑到新的全局任务。
        task = getattr(self.box, 'current', self.box.task)
        if task.cancelled.is_set(): return 'no'
        box = {'event': threading.Event(), 'answer': 'timeout'}
        self.bridge.ask.emit(spec, box)
        deadline = monotonic()+APPROVAL_TIMEOUT
        while not box['event'].wait(.1):
            if task.cancelled.is_set(): return 'no'
            if monotonic() >= deadline:
                self.bridge.event.emit('approval_timeout', {})
                return 'timeout'
        return 'no' if task.cancelled.is_set() else box['answer']

    def _approve_tool(self, kind: str, title: str, detail: str) -> str:
        """工具要你点头时（后台线程）：弹确认气泡，返回 once / always / no / timeout。"""
        if self.auto_approve: return self.auto_approve(kind, title, detail)
        label, can_remember = CHOICES.get(kind, (None, False))
        choices = [('可以', 'once')] + ([(label, 'always')] if can_remember and label else []) + [('不行', 'no')]
        answer = self._wait_ui({'text': title, 'choices': choices, 'details': detail, 'log': title.replace('\n', ' ')})
        if self.worker is not None:
            self.bridge.event.emit('approved', {'tool': kind, 'decision': answer, 'title': title})
        return answer

    def _tool_record(self, record: dict):
        """自己的工具每调用一次记一步审计（参数、审批结果、结果或错误）。"""
        import json
        state = self.run_state
        if not state.get('run_id'): return
        state['seq'] += 1
        database = self.window.database
        step_id = database.add_agent_step(state['run_id'], state['seq'], record['tool'], json.dumps(record['args'], ensure_ascii=False)[:4000],
                                          'tool', record['decision'])
        database.finish_agent_step(step_id, record.get('result', '')[:500], record.get('error', '')[:500])

    def _ask(self, spec, box):
        w = self.window
        bubble = w.pet.ask(spec['text'][:420], spec['choices'], spec.get('details', ''))
        def chosen(value):
            box['answer'] = value; box['event'].set()
            w.pet.dispatch('waiting', active=False)
        bubble.chosen.connect(chosen)
        w.pet.dispatch('waiting', active=True)
        w._append_message('确认', spec.get('log') or spec['text'])
        if w.pet.isHidden(): w.pet.summon()

    # ---- 进度 -----------------------------------------------------------------------
    def _event(self, kind, data):
        w = self.window
        if kind == 'result':
            self.got_result = True; self._done(data); return
        if kind == 'tool':
            w.pet.show_activity(ACTIVITY.get(data.get('tool'), 'computer'))
            w.pet.dispatch('work_started', tool=data.get('tool', ''), task=self.goal)
            w._status(f'正在{tool_label(data.get("tool", ""))}：'+data.get('summary', '').split('：', 1)[-1][:40])
            w._append_message('步骤', f'{tool_label(data.get("tool", ""))}：'+data.get('summary', '').split('：', 1)[-1][:120])
        elif kind == 'approved':
            mark = {'once': '✓ 同意', 'always': '✓ 本次都同意', 'no': '✗ 拒绝'}.get(data['decision'], '… 没有回应')
            w._append_message('步骤', f'{mark}：'+data.get('title', '').split('\n', 1)[-1][:80])
        elif kind == 'approval_timeout':
            w.pet.close_confirm(); w.pet.dispatch('waiting', active=False)

    def _done(self, result):
        w = self.window
        w.pet.hide_activity(); w.pet.close_confirm(); w.pet.dispatch('waiting', active=False)
        ok = result.status == 'done'
        w.pet.dispatch('work_finished', ok=ok, error=result.text if not ok else '')
        # 最终回复里的表情 / 动作标注：执行后从文字里去掉（和聊天一致）
        from aicompanion.pet.script import CueStream
        text, cues = CueStream(w.vocab).feed(result.text, final=True)
        for cue in cues:
            if cue.kind != 'task': w.pet.perform(cue)
        result.text = text.strip() or '做完了。'
        w.pet.show_bubble(result.text, 14, typewriter=True, kind='reply')
        w._append_message(w.controller.character.name, result.text
                          + f'\n（{len(result.steps)} 步 · {result.tokens} 词元 · 约 {result.cost:.3f} 元）')
        # 结果写进聊天记录，之后聊天时她知道自己做过什么
        w.database.add_message('assistant', f'[干活] {self.goal}\n{result.text}', w.controller.character.id, source='agent')
        w.speak_reply(result.text, [c.name for c in cues if c.kind == 'expression'])
        if result.tokens > TOKEN_BURN:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(16000, lambda: w.pet.say_line('token_burn', force=True))
        w._status('就绪')
        self.finished.emit(result)

    def _failed(self, error):
        self.got_result = True
        w = self.window
        w.pet.hide_activity(); w.pet.close_confirm()
        w.pet.dispatch('work_finished', ok=False, error=error)
        w.pet.show_bubble('呜，出错了：'+error[:80], 10); w._status('任务失败：'+error)

    def _worker_finished(self):
        w = self.window
        worker, self.worker = self.worker, None
        w.pet.agent_busy = False
        if not getattr(self, 'got_result', True):
            # 线程异常退出且没有结果：也要把界面从"干活中"恢复过来
            w.pet.hide_activity(); w.pet.close_confirm(); w.pet.dispatch('waiting', active=False)
            w.pet.dispatch('work_finished', ok=False, error='任务中断'); w._status('就绪')
        if worker:
            w.tasks.discard(worker); worker.deleteLater()
        w._finish_exit_if_ready()
