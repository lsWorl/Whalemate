"""把 DeepSeek Harness 接到桌宠：后台线程跑任务，进度用状态机和活动图标表现。

DSH 负责想和调度（还有网页搜索、分身）；读写文件、删除、命令由本进程的工具执行（agent/tools_server.py），
需要你同意的步骤变成确认气泡。这里负责：人设口吻、选工作区和放开的文件夹、确认气泡、进度、用量和额度、审计记录。
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer, Qt
from aicompanion.agent.approval import MODES, Rules
from aicompanion.bridge.tasks import TaskBridge, authorized

from aicompanion.agent import attachments as task_files
from aicompanion.agent.dsh_backend import DshAgent, pick_workspace, problem
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


def tool_label(name: str) -> str:
    return TOOL_NAMES.get(name, name)


class _Bridge(QObject):
    event = Signal(str, object)
    offered = Signal(object)
    changed = Signal()


class AgentSession(QObject):
    finished = Signal(object)             # DshResult（附 run_id / tokens / cost）

    def __init__(self, window):
        super().__init__(window)
        self.window = window; self.worker = None; self.goal = ''
        self.dsh = DshAgent()
        self.box = ToolBox(secrets=self._secrets())
        self.box.reserve_effect = window.database.reserve_agent_effect
        self.box.peer_valid = lambda peer: authorized(window.database,peer)
        self.box.request_approval = self._request; self.box.on_call = self._tool_record
        self.server = McpToolServer(self.box)
        self.run_state = {'run_id': 0, 'seq': 0}
        self.bridge = _Bridge(self)
        self.tasks = window.controller.qq_tasks = TaskBridge(window.database, self.bridge.offered.emit,self.bridge.changed.emit)
        self.bridge.offered.connect(window._qq_offer)
        self.bridge.changed.connect(self._refresh_approvals, Qt.ConnectionType.QueuedConnection)
        self.displayed = None
        self.offer_callbacks = {}
        self.audit_lock = threading.Lock()
        QTimer.singleShot(60_000, lambda: threading.Thread(target=self.housekeep, daemon=True, name='trash-clean').start())
        self.bridge.event.connect(self._event)
        self.auto_approve = None   # 自检时替换成函数

    @property
    def busy(self) -> bool:
        return self.worker is not None or self.box.working

    # ---- 开始 / 停止 ---------------------------------------------------------------
    def start(self, goal: str, announce: bool = True, *, paused_run=None, attachments=(), remote=None) -> bool:
        w = self.window
        if getattr(self,'offer_callbacks',None):
            w._status('请先回应当前办事提议。'); return False
        if w.closing or (remote and not w.controller.agent_enabled): return False
        if self.busy: w.pet.show_bubble('我手上还有活没做完，等我一下～', 5); return False
        missing = problem() or dependency_problem()
        if missing: w.pet.show_bubble(missing, 8); w._status(missing); return False
        target = w.controller.router.agent_target(w.controller.character.id)
        if target is None:
            problem_text = getattr(w.controller.router, 'agent_problem', lambda cid: '')(w.controller.character.id) \
                or '还没有配置办事模型，请到设置的“模型与 Key”页选择。'
            w.pet.show_bubble(problem_text, 12); w._status(problem_text); return False
        if w.quota.check_silent('agent') == 'stop':
            w.pet.show_bubble('今天"干活"的词元额度用完了，明天再帮你做吧。', 8); return False
        database = w.database; character = w.controller.character
        peer = remote.peer if remote else None
        received = remote.files if remote else ()
        if paused_run is not None:
            with database.transaction() as c:
                bound = c.execute('SELECT bot,owner FROM agent_qq_runs WHERE run_id=?',(paused_run,)).fetchone()
            peer = tuple(bound) if bound else None
        if peer and not authorized(database,peer):
            w._status('这项 QQ 交办的主人绑定已失效。')
            return False
        self.goal = goal
        cwd = pick_workspace(goal, database.get_setting('dsh_workspace', ''))
        rows = database.agent_attachments(paused_run) if paused_run is not None else []
        if rows:
            cwd = Path(rows[0]['copy']).parents[2]
        try:
            files = ([(Path(name),len(data)) for name,data in received] if received else
                     task_files.selection(attachments,self.box.secrets))
        except (ValueError, OSError) as exc:
            w.pet.show_bubble(str(exc), 8); w._status(str(exc)); return False
        model = target.model_id
        allowed = self._allowed(goal, cwd)
        resuming = paused_run is not None
        prompt = self._prompt(goal, cwd, allowed, resuming=resuming)
        if resuming:
            run_id = paused_run
            seq = database.resume_agent_run(run_id, character.id)
            if seq is None: return False
        else:
            run_id = database.add_agent_run(character.id, goal, attachments=files, workspace=cwd, peer=peer)
            if files:
                rows = database.agent_attachments(run_id)
            seq = 0
        state = self.run_state = {'run_id': run_id, 'seq': seq}
        task = self.box.task = Task(run_id=run_id, allowed=allowed, resuming=resuming,
                                    originals={Path(row['original']): Path(row['copy']) for row in rows if not peer},
                                    rules=Rules(database,cwd),peer=peer,
                                    mode=database.get_setting('approval_mode','ask'),
                                    copies={Path(row['copy']) for row in rows})

        selector = getattr(w,'approval_mode',None)
        if selector is not None and selector.currentData() in MODES:
            task.mode = selector.currentData()
        if peer:
            self.tasks.notify(peer,'我开始办了。'+goal[:100])

        def on_event(kind, data):
            if kind == 'usage':
                with meter.using('agent', character.id, f'agent:{run_id}'):
                    meter.report(model, {'prompt_tokens': data['prompt'], 'completion_tokens': data['completion'],
                                         'prompt_cache_hit_tokens': data['cached']})
                if w.quota.check_silent('agent') == 'stop': self.dsh.stop()   # 额度用完立即停
            elif kind == 'tool' and data['tool'] not in OWN_TOOLS:
                # 自己的工具由 _tool_record 记（带审批结果）；DSH 自带的（网页、分身等）在这里记
                with self.audit_lock:
                    state['seq'] += 1
                    seq = state['seq']
                step_id = database.add_agent_step(run_id, seq, data['tool'], data['summary'][:4000], 'dsh', 'auto')
                database.finish_agent_step(step_id, data['summary'][:200])
            self.bridge.event.emit(kind, data)

        def run(token):
            try:
                if peer and not authorized(database, peer):
                    raise PermissionError('主人绑定已失效')
                active_prompt = prompt
                if rows:
                    from aicompanion.bridge.media import QQMedia
                    from aicompanion.services import budget
                    media = QQMedia(database, w.controller)
                    def describe(data, mime):
                        if peer and not authorized(database, peer):
                            raise PermissionError('主人绑定已失效')
                        if w.quota.check_silent('agent') == 'stop':
                            raise ValueError('今天的办事额度已用完')
                        with meter.using('agent', character.id, f'agent:{run_id}'):
                            return media.describe_task_image(data, mime, goal, character.id)
                    previews, task.captions = task_files.prepare(
                        database, rows, resuming=resuming, secrets=self.box.secrets, cancelled=task.cancelled,
                        describe=describe, received=received)
                    if w.quota.check_silent('agent') == 'stop':
                        raise ValueError('今天的办事额度已用完')
                    active_prompt, report = task_files.prompt_for(prompt, rows, previews, target)
                    budget.save(database, character.id, report)
                    task.read_chars = max(512, min(task.read_chars, report.limit//4))
                self.dsh.mcp_servers = [self.server.acp_entry()]
                if task.cancelled.is_set():
                    raise ValueError('任务已停止')
                if peer and not authorized(database,peer):
                    raise PermissionError('主人绑定已失效')
                result = self.dsh.run(active_prompt, cwd, target, on_event)
            except Exception as error:
                database.finish_agent_run(run_id, 'cancelled' if task.cancelled.is_set() else 'failed',
                                          '', str(error)[:12000], state['seq'], *self._usage(run_id))
                if rows:
                    self.housekeep()
                raise
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
        w.task_card.detail.setText(goal)
        w.task_card.clear_approval()
        w.task_card.show()
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
            try:
                task_files.recycle_finished(database, self.box.trash)
                attachment_error = ''
            except (OSError, ValueError) as error:
                attachment_error = str(error)
            report = housekeeping.clean(float(database.get_setting('trash_keep_days', housekeeping.DEFAULT_KEEP_DAYS) or 0),
                                        int(float(database.get_setting('trash_max_mb', 2048) or 2048)*1024*1024),
                                        [database.get_setting('blog_root', '')], dsh_home())
            database.set_setting('trash_last_clean', report.text()+attachment_error)
            return report
        except Exception as error:
            return error

    # ---- 放开的文件夹、私密位置 -------------------------------------------------------------
    def _secrets(self):
        """她的工具碰不到的位置：项目的 .env、数据库所在文件夹、DSH 的会话记录、本应用的本机数据。"""
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

    def _prompt(self, goal: str, cwd, allowed=(), *, resuming=False) -> str:
        """交给 DSH 的输入：人设 + 环境说明 + 最近聊天 + 用户原话。"""
        w = self.window; character = w.controller.character; database = w.database
        blog = database.get_setting('blog_root', '')
        project = Path(__file__).resolve().parents[3]
        from aicompanion.domain.bond import current as current_bond
        bond = current_bond(database, character.id)
        from aicompanion.pet.script import SAY_PROMPT
        lines = [bond.fill(f'你是{character.name}。{(character.description or "")[:900]}', character.name), bond.prompt(),
                 bond.fill(f'说话方式：{(character.speaking_style or "")[:600]}', character.name),
                 '最后给用户的回复要保持上面的人设：一两句口语，像同事口头交代做了什么、结果在哪，不写报告、不列清单、不用 Markdown 标题，除非用户要细节。',
                 SAY_PROMPT.strip(),
                 '做事要求：用户的电脑是 Windows。看文件、改文件、删文件、跑 PowerShell 都用 aicompanion 开头的那组工具；'
                 '尽量在后台完成，不要让用户手动操作；用户点名的路径、文件名、程序就用那个，不要擅自换；一句话里有几件事要按顺序全部做完。',
                 f'当前工作区：{cwd}。放开写的文件夹：{"；".join(str(a) for a in allowed) or "无"}。'
                 '工具会根据当前办事模式和项目规则审批。删除和越界改动需要用户逐项确认，无需你重复询问。'
                 '如果工具返回"被拒绝"，就停下来如实告诉用户，不要换个办法绕过去，也不要教用户怎么绕过。密钥文件和我的数据库是读不到的，也别去找。'
                 '看 git 差异时写 `git diff --no-ext-diff --no-textconv`（show 同样；blame 加 --no-textconv），这样不用弹窗。']
        if blog: lines.append(f'用户的 Hexo 博客在 {blog}：草稿放 source/_drafts，本地预览用 `npx hexo server --draft -p 4000`（后台启动，别阻塞）。')
        if (project/'docs'/'implementation_status.md').exists():
            lines.append(f'AICompanion 项目在 {project}，写开发日志的素材在 docs/implementation_status.md。')
        history = [] if resuming else self._history(goal)
        if resuming:
            lines.append('这是一项暂停过的任务。只处理用户刚确认的剩余事项。先检查现状，已做过或结果不明的步骤不要重试。')
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
    def _request(self, kind, title, detail, rule):
        task = self.box.current
        if task.cancelled.is_set(): return 'no',''
        if self.auto_approve:
            return self.auto_approve(kind,title,detail), ''
        req = self.tasks.approvals.add(task.run_id,kind,title,detail,rule,task.peer or self.tasks.current_peer(),APPROVAL_TIMEOUT)
        self._send_request(req)
        while not req.ready.wait(.1):
            if task.cancelled.is_set() or (task.peer and not authorized(self.window.database,task.peer)):
                self.tasks.approvals.answer(req.code,'no')
            elif self.tasks.clock()>=req.deadline:
                self.tasks.approvals.answer(req.code,'timeout')
        return req.decision, req.reason

    def _send_request(self, req):
        if req.peer:
            choices = '同意 编号 / 拒绝 编号 原因'
            if req.rule:
                choices += ' / 同意这类 编号 / 以后都允许 编号 / 同意全部'
            self.tasks.notify(req.peer,req.describe()+'\n'+req.detail+'\n'+choices,req)

    def confirm_offer(self, goal, peer, callback, *, kind='offer'):
        title = '要我接着办吗？请核对已做步骤。' if kind=='resume' else '要我现在动手吗？'
        req = self.tasks.approvals.add(0,kind,title+'\n'+goal,peer=peer,timeout=APPROVAL_TIMEOUT)
        self.offer_callbacks[req.code] = (req,callback)
        self._send_request(req)
        QTimer.singleShot(int(APPROVAL_TIMEOUT*1000)+1,lambda:self.tasks.approvals.answer(req.code,'timeout'))

    def _refresh_approvals(self):
        w = self.window
        for code,(req,callback) in list(self.offer_callbacks.items()):
            if req.ready.is_set():
                del self.offer_callbacks[code]
                callback(req.decision)
        items = self.tasks.approvals.items()
        card = w.task_card
        req = items[0] if items else None
        card.queue_count.setText(f'还有 {len(items)} 项待确认')
        card.queue_count.setVisible(bool(items))
        card.batch.setVisible(bool(req and any(i.rule and i.run == self.box.task.run_id for i in items)))
        if self.displayed is req:
            return
        if self.displayed:
            self.displayed = None
            w.pet.close_confirm()
            card.clear_approval()
            w.pet.dispatch('waiting',active=False)
        if not req or w.closing:
            if not self.busy: card.hide()
            return
        self.displayed = req
        choices = [('同意这一次','once')]
        if req.rule:
            choices += [('本次任务都同意这类','always'),('以后都允许','persist')]
        choices.append(('拒绝','no'))
        bubble = w.pet.ask(req.describe(),choices,req.detail)
        def answer(value):
            reason = ''
            if value=='no':
                from PySide6.QtWidgets import QInputDialog
                reason, _ = QInputDialog.getText(w,'拒绝操作','原因（可留空）')
            self.tasks.approvals.answer(req.code,value,reason)
        bubble.chosen.connect(answer)
        card.detail.setText(req.describe())
        card.details.setPlainText(req.detail)
        card.details.setVisible(bool(req.detail))
        card.answer = bubble.choose
        card.actions.show()
        card.show()
        card.reuse.setVisible(bool(req.rule))
        card.persist.setVisible(bool(req.rule))
        w.pet.dispatch('waiting',active=True)
        if w.pet.isHidden(): w.pet.summon()

    def approve_batch(self):
        from PySide6.QtWidgets import QMessageBox
        items = [r for r in self.tasks.approvals.items() if r.rule and r.run==self.box.task.run_id]
        if not items:
            return
        details = '\n\n'.join(r.describe() + '\n' + r.detail for r in items)
        choice = QMessageBox.question(self.window, '确认以下操作', details,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if choice == QMessageBox.StandardButton.Yes:
            self.tasks.approvals.batch([r.code for r in items],items[0].run)

    def _tool_record(self, record: dict):
        """自己的工具每调用一次记一步审计（参数、审批结果、结果或错误）。"""
        import json
        state = self.run_state
        run_id = record.get('run_id') or state.get('run_id')   # 记在调用开始时的任务名下：迟到的回调不会算进下一个任务
        if not run_id: return
        with self.audit_lock:
            if run_id == state.get('run_id'): state['seq'] += 1; seq = state['seq']
            else: seq = 0
        database = self.window.database
        step_id = database.add_agent_step(run_id, seq, record['tool'], json.dumps(record['args'], ensure_ascii=False)[:4000],
                                          'tool', record['decision'])
        database.finish_agent_step(step_id, record.get('result', '')[:500], record.get('error', '')[:500])

    # ---- 进度 -----------------------------------------------------------------------
    def _event(self, kind, data):
        w = self.window
        if kind == 'result':
            self.got_result = True; self._done(data); return
        if kind == 'tool':
            if not self.displayed:
                w.task_card.detail.setText(f'正在{tool_label(data.get("tool", ""))} · ' + data.get('summary', '')[:120])
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
        if ok:
            # 计划书 v4 功能三：一起把事办成了，她会开心一阵
            try:
                from aicompanion.services.mood_service import MoodService
                MoodService(w.database).event(w.controller.character.id, 'achievement', reason=f'一起办完了：{self.goal[:20]}')
            except Exception: pass
        # 最终回复里的表情 / 动作标注：执行后从文字里去掉（和聊天一致）
        from aicompanion.pet.script import CueStream
        text, cues = CueStream(w.vocab).feed(result.text, final=True)
        for cue in cues:
            if cue.kind != 'task': w.pet.perform(cue)
        result.text = text.strip() or '做完了。'
        peer = getattr(self.box.task,'peer',None)
        if peer:
            from aicompanion.bridge.tasks import result_notice
            self.tasks.notify(peer, result_notice(ok, result.text, result.spoken_summary))
        w.pet.show_bubble(result.text, 14, typewriter=True, kind='reply')
        w._append_message(w.controller.character.name, result.text
                          + f'\n（{len(result.steps)} 步 · {result.tokens} 词元 · 约 {result.cost:.3f} 元）')
        # 结果写进聊天记录，之后聊天时她知道自己做过什么
        w.database.add_message('assistant', f'[干活] {self.goal}\n{result.text}', w.controller.character.id, source='agent')
        w.speak_reply(result.text, [c.name for c in cues if c.kind == 'expression'], result.spoken_summary)
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
        if self.box.task.peer:
            self.tasks.notify(self.box.task.peer,'这件事没办成，详细见桌面办事记录。')
        w.pet.show_bubble('呜，出错了：'+error[:80], 10); w._status('任务失败：'+error)

    def _worker_finished(self):
        w = self.window
        worker, self.worker = self.worker, None
        w.task_card.clear_approval()
        w.task_card.hide()
        w.pet.agent_busy = False
        if not getattr(self, 'got_result', True):
            # 线程异常退出且没有结果：也要把界面从"干活中"恢复过来
            w.pet.hide_activity(); w.pet.close_confirm(); w.pet.dispatch('waiting', active=False)
            w.pet.dispatch('work_finished', ok=False, error='任务中断'); w._status('就绪')
            if self.box.task.peer:
                self.tasks.notify(self.box.task.peer, '任务中断了，详细见桌面办事记录。')
        if worker:
            w.tasks.discard(worker); worker.deleteLater()
        w._finish_exit_if_ready()
