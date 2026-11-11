"""干活交给 DeepSeek Harness（DSH）负责"想"，真正动手的工具由我们自己执行（tools_server.py）。

为什么这样分：DSH 0.2.1rc2 在 Windows 上没有可用的沙箱和审批通道，exFAT 盘更是没有任何权限控制（实测它能直接写工作区外、
直接永久删除）。所以照 Claude Code / Codex 的做法，在执行前把关：

- 运行时：pip 包 deepseek-harness-sdk 自带的 dsh 运行时，用 ACP 模式（Agent Client Protocol，Zed 等编辑器用的协议）
  在后台子进程里跑，以 Windows 低完整性级别启动；
- 工具：补丁关掉 DSH 自带的写文件、PowerShell、bash、搜文件、后台任务、代码工作流；读写文件和命令改由本进程的
  MCP 服务提供，每一步按规则放行或弹确认气泡（见 tools_server.py）。网页搜索 / 读取、分身、待办这些不碰本机的工具保留；
- 停止：ACP 的 session/cancel，真正的中断；兜底再关进程；
- 用量：会话结束后读 DSH 的会话投影缓存里的 tokenUsage（未缓存输入、缓存命中、输出）；
- 隐私：关闭"随请求上传会话日志"和遥测；命令子进程里去掉各类 *_API_KEY。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Callable

from aicompanion.agent.paths import protected_reason, task_folders

DEFAULT_MODEL = 'deepseek-flash'   # 只在测试里不传 target 时用
MCP_PREFIX = 'mcp__aicompanion__'
DISABLED_ROWS = ('tool-pwsh', 'tool-bash', 'persistent-pwsh', 'persistent-bash', 'tool-fs', 'tool-fs-search', 'tool-jobs',
                 'tool-workflow', 'ptc-runtime')
PATCH = ("# AICompanion：不随请求上传会话日志；关掉自带的改文件 / 命令行 / 搜文件工具（改用 AICompanion 的 MCP 工具，执行前把关）\n"
         "- id: session-log-deepseek\n  config:\n    enabled: false\n"
         + ''.join(f'- id: {row}\n  disabled: true\n' for row in DISABLED_ROWS))
PROMPT_TIMEOUT = 30*60
CREDENTIALS_DIR = 'aicompanion-credentials'   # 本应用自己的 managed 凭据目录，每次启动清空


# llm-pi-ai 内置目录里没有、需要在补丁里声明的模型（MiniMax 官方文档：100 万上下文，支持图片，思考关不掉；
# 不声明思考档位，请求里就不带 thinking 参数，由服务端按默认深度思考——带 disabled 会被拒绝）
MODEL_SPECS = {'MiniMax-M3.1-Flash': {'contextWindow': 1_000_000, 'maxTokens': 131072, 'input': ['text', 'image']}}


def runtime_patch(home: Path, target=None) -> str:
    """运行时补丁。

    凭据：DSH 默认先读 `$DSH_HOME/.credentials.yaml`（managed，优先级高于环境变量），里面若有旧 Key 会压过设置页选定的 Key
    （Codex 审计 R2，实测复现）。这里把 managed 文件改指到本应用自己的空文件，用户原有的凭据文件不读、不删。

    MiniMax 办事：Harness 的 llm-pi-ai 多供应商路由默认不注册任何路由，要在配置里给出供应商才会激活。只激活本次选定的那一条，
    Key 按 apiKeyEnv 从环境变量取（只有选定的那一把），地址用账户主机的 Anthropic 兼容接口。"""
    from aicompanion.providers.router import AGENT_MODELS, AGENT_ROUTES, PI_AI_ROUTES
    store = (Path(home)/CREDENTIALS_DIR/'.credentials.yaml').as_posix()
    text = PATCH + f"- id: credentials\n  config:\n    path: {json.dumps(store)}\n    watch: false\n"
    route = getattr(target, 'route', '')
    if route in PI_AI_ROUTES:
        models = ''.join(f"          - id: {json.dumps(m)}\n" + ''.join(f"            {k}: {json.dumps(v)}\n" for k, v in MODEL_SPECS.get(m, {}).items())
                         for m in AGENT_MODELS[route])
        base = f"        baseURL: {json.dumps(target.base_url)}\n" if getattr(target, 'base_url', '') else ''
        text += (f"- id: llm-pi-ai\n  config:\n    providers:\n      {route}:\n        apiKeyEnv: {AGENT_ROUTES[route][0]}\n"
                 + base + "        models:\n" + models)
    return text


def prepare_home(home: Path) -> Path:
    """启动前准备：清空本应用的 managed 凭据文件，返回运行时的当前目录（本应用专用的空目录）。
    DSH 还会把“当前目录/.env”和“$DSH_HOME/.env”当兜底凭据来源；当前目录由我们建、不放 .env，
    `$DSH_HOME/.env` 存在时拒绝启动并提示（那是用户放的文件，不替用户删除）。"""
    home = Path(home)
    if (home/'.env').exists():
        raise RuntimeError(f'办事运行时目录里有 .env 文件（{home}），里面的 Key 可能被 Harness 读到；请先把它移走再试。')
    store = home/CREDENTIALS_DIR; store.mkdir(parents=True, exist_ok=True)
    managed = store/'.credentials.yaml'; managed.write_text('', encoding='utf-8')
    try: os.chmod(managed, 0o600)   # 运行时要求凭据文件不能对其他用户可读（Windows 上无影响）
    except OSError: pass
    run = home/'run'; run.mkdir(exist_ok=True)
    if (run/'.env').exists():
        raise RuntimeError(f'办事运行时目录里有 .env 文件（{run}），请先把它移走再试。')
    return run


def dsh_home() -> Path:
    """DSH 的数据目录，放在 LocalLow（低完整性进程能写的位置）。"""
    from aicompanion.agent.sandbox import low_root
    return low_root()/'dsh'


def problem() -> str:
    """运行时是否可用；空串表示可用。"""
    try:
        import importlib.util
        if importlib.util.find_spec('deepseek_harness_runtime') is None:
            return '还没有安装 DeepSeek Harness，请运行 tools\\setup_dsh.bat'
    except (ImportError, ValueError):
        return '还没有安装 DeepSeek Harness，请运行 tools\\setup_dsh.bat'
    return ''


def pick_workspace(goal: str, default: str) -> Path:
    """任务里点名的第一个存在的文件夹；否则默认工作区；都不行就用户主目录。受保护的位置不当工作区。"""
    for folder in task_folders(goal):
        if folder.is_dir(): return folder
        if folder.parent.is_dir() and not protected_reason(folder.parent): return folder.parent
    path = Path(default).expanduser() if default else Path.home()
    return path if path.is_dir() and not protected_reason(path.resolve()) else Path.home()


# 运行时需要的系统变量（Windows 进程、代理、编码）。其余一概不传：父进程里可能有各家的 API Key，
# DSH 内置多家供应商路由，会读到它们（计划书 v4 硬底线：Harness 子进程只拿到本次选定的 Key）。
ENV_KEEP = ('SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'HOME',
            'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'COMMONPROGRAMFILES',
            'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER', 'OS', 'USERNAME', 'USERDOMAIN',
            'COMPUTERNAME', 'LANG', 'LC_ALL', 'TZ', 'PYTHONIOENCODING', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
            'http_proxy', 'https_proxy', 'no_proxy')


def child_environment(selected: tuple, parent=None) -> dict:
    """白名单里的系统变量 + 本次选定的凭据。大小写不敏感地匹配（Windows 环境变量不分大小写）。"""
    parent = os.environ if parent is None else parent
    keep = {name.upper() for name in ENV_KEEP}
    env = {k: v for k, v in parent.items() if k.upper() in keep}
    env.update(dict(selected))
    return env


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    cached: int = 0

    @property
    def total(self) -> int: return self.prompt+self.completion


@dataclass
class DshResult:
    status: str                 # done / failed / cancelled / limit
    text: str
    steps: list = field(default_factory=list)   # [(工具名, 一句话, 参数)]
    usage: Usage = field(default_factory=Usage)
    raw: str = ''               # 失败时的运行时输出（写进审计记录，方便排查）


def tool_name(title: str) -> str:
    return title[len(MCP_PREFIX):] if title.startswith(MCP_PREFIX) else title


def describe_tool(name: str, data: dict) -> str:
    """把工具调用变成一句话（审计和进度显示用）。"""
    target = (data.get('command') or data.get('path') or data.get('source') or data.get('pattern') or data.get('url')
              or data.get('query') or '')
    return f'{name}：{str(target)[:120]}' if target else name


class AcpConnection:
    """和 dsh 运行时之间的 ACP（JSON-RPC，一行一条）连接。测试时可以换成假的 transport。"""

    def __init__(self, proc):
        self.proc = proc; self.lock = threading.Lock(); self.next_id = 0; self.waiters: dict = {}
        self.listeners: list[Callable[[dict], None]] = []; self.requests: Callable[[dict], dict | None] = lambda m: None
        self.stderr: list[str] = []
        threading.Thread(target=self._read, daemon=True, name='dsh-acp-reader').start()
        if getattr(proc, 'stderr', None) is not None:
            threading.Thread(target=self._read_stderr, daemon=True, name='dsh-acp-stderr').start()

    def _send(self, message: dict):
        data = json.dumps(message, ensure_ascii=False)+'\n'
        with self.lock:
            self.proc.stdin.write(data); self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 60):
        with self.lock:
            self.next_id += 1; rid = self.next_id; box = queue.Queue(); self.waiters[rid] = box
        self._send({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
        try: reply = box.get(timeout=timeout)
        except queue.Empty: raise TimeoutError(f'{method} 超时')
        finally: self.waiters.pop(rid, None)
        if isinstance(reply, BaseException): raise reply
        if 'error' in reply: raise RuntimeError(f'{method}：{(reply["error"] or {}).get("message")}')
        return reply.get('result') or {}

    def notify(self, method: str, params: dict):
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _read(self):
        try:
            for line in self.proc.stdout:
                try: message = json.loads(line)
                except ValueError: continue
                if 'id' in message and 'method' in message:
                    try: result = self.requests(message)
                    except Exception: result = None
                    reply = {'jsonrpc': '2.0', 'id': message['id']}
                    if result is None: reply['error'] = {'code': -32601, 'message': 'not supported'}
                    else: reply['result'] = result
                    self._send(reply)
                elif 'id' in message:
                    box = self.waiters.get(message['id'])
                    if box: box.put(message)
                else:
                    for listener in list(self.listeners):
                        try: listener(message)
                        except Exception: pass
        finally:
            closed = ConnectionError('DSH 运行时退出了'+(('：'+'\n'.join(self.stderr[-5:])) if self.stderr else ''))
            for box in list(self.waiters.values()): box.put(closed)

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip()); del self.stderr[:-200]

    def close(self):
        try: self.proc.stdin.close()
        except Exception: pass
        try:
            self.proc.terminate(); self.proc.wait(timeout=3)
        except Exception:
            try: self.proc.kill()
            except Exception: pass


class DshAgent:
    """一个 dsh 运行时（ACP），每个任务开一个新会话。所有方法都在后台线程调用。"""

    def __init__(self, launcher: Callable[[dict], object] | None = None):
        self.launcher = launcher        # 测试时换成假的进程
        self.conn: AcpConnection | None = None; self.key = None; self.lock = threading.Lock()
        self.mcp_servers: list[dict] = []   # mcp_server.McpToolServer.acp_entry()
        self.session_id = None; self.stopping = False; self.done = threading.Event()

    # ---- 运行时 -----------------------------------------------------------------
    def _launch(self, selected: tuple, target=None):
        home = dsh_home(); home.mkdir(parents=True, exist_ok=True)
        patch = home/'aicompanion.patch.yml'; patch.write_text(runtime_patch(home, target), encoding='utf-8')
        run = prepare_home(home)
        env = child_environment(selected)
        env.update({'DSH_HOME': str(home), 'DSH_TELEMETRY_DISABLED': '1', 'DSH_PERMISSION_MODE': 'workspace-write'})
        if self.launcher: return self.launcher(env)
        from aicompanion.agent import sandbox
        from deepseek_harness_runtime import resolve_bundled_launch_args
        env = sandbox.child_env(env)
        args = [*resolve_bundled_launch_args(), '--profile', 'acp', '--patch', str(patch)]
        def start():
            # 运行时的当前目录固定在 C 盘数据目录下（run 子目录）：打包的虚拟路径按当前盘拼接，放 D 盘会找不到 koffi.node（实测）
            return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                    env=env, cwd=str(run), bufsize=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if sandbox.IS_WINDOWS:
            with sandbox.low_integrity(): return start()   # 运行时和它的子进程都是低完整性
        return start()

    def _ensure(self, selected: tuple, target=None) -> AcpConnection:
        """凭据、路由或地址变了（换 Key、换路由）就重启运行时；进程启动时的环境变量和补丁之后改不了。"""
        with self.lock:
            key = (getattr(target, 'route', ''), getattr(target, 'base_url', ''), tuple(selected))
            if self.conn is not None and self.key == key and self.conn.proc.poll() is None: return self.conn
            self._close_locked()
            conn = AcpConnection(self._launch(tuple(selected), target))
            conn.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}}, 60)
            self.conn, self.key = conn, key
            return conn

    def _close_locked(self):
        conn, self.conn, self.key = self.conn, None, None
        if conn is not None: conn.close()

    def close(self):
        with self.lock: self._close_locked()

    def stop(self):
        """停下当前任务：先发 session/cancel（DSH 会在当前一步结束后停），5 秒没停就关掉运行时。"""
        self.stopping = True
        conn, sid = self.conn, self.session_id
        if conn and sid:
            try: conn.notify('session/cancel', {'sessionId': sid})
            except Exception: pass
        def later():
            if not self.done.wait(5): self.close()
        threading.Thread(target=later, daemon=True, name='dsh-stop').start()

    # ---- 用量 -----------------------------------------------------------------------
    @staticmethod
    def _usage(session_id: str) -> Usage | None:
        """DSH 的会话投影缓存里有累计用量（一个任务一个会话，所以就是这次任务的用量）。"""
        path = dsh_home()/'storages'/'session_projcache'/'sessions'/f'{session_id}.json'
        for _ in range(4):
            try:
                totals = json.loads(path.read_text(encoding='utf-8'))['record']['rows']['tokenUsage']['val']['totals']
                fresh, cache, out = int(totals.get('uncachedInputTokens') or 0), int(totals.get('cacheReadTokens') or 0), int(totals.get('outputTokens') or 0)
                return Usage(fresh+cache, out, cache)
            except (OSError, ValueError, KeyError, TypeError):
                time.sleep(0.25)
        return None

    # ---- 跑一个任务 ----------------------------------------------------------------
    def run(self, prompt: str, cwd: Path, target=None, on_event: Callable[[str, dict], None] | None = None) -> DshResult:
        """target：providers.router.AgentTarget（路由、模型、只含选定凭据的环境变量）。测试可不传。"""
        route, model, selected = (target.route, target.model_id, target.env) if target else ('deepseek-official', DEFAULT_MODEL, ())
        on_event = on_event or (lambda kind, data: None)
        self.stopping = False; self.done.clear(); self.session_id = None
        steps: list = []; text: list[str] = []; context = {'used': [], 'chars': 0, 'final_from': 0}; calls: dict = {}

        def listen(message):
            params = message.get('params') or {}
            if params.get('sessionId') != self.session_id: return
            update = params.get('update') or {}; kind = update.get('sessionUpdate')
            if kind == 'agent_message_chunk':
                chunk = (update.get('content') or {}).get('text') or ''
                if chunk: text.append(chunk); context['chars'] += len(chunk); on_event('text', {'text': chunk})
            elif kind == 'tool_call':
                name = tool_name(str(update.get('title') or 'tool')); args = update.get('rawInput') or {}
                calls[update.get('toolCallId')] = name
                context['final_from'] = len(text)   # 工具调用前说的话是过程，最后回复只取最后一次工具调用之后的
                context['chars'] += len(json.dumps(args, ensure_ascii=False))
                line = describe_tool(name, args if isinstance(args, dict) else {})
                steps.append((name, line, args)); on_event('tool', {'tool': name, 'summary': line})
            elif kind == 'tool_call_update' and update.get('status') in ('completed', 'failed'):
                on_event('step', {'ok': update.get('status') == 'completed', 'tool': calls.get(update.get('toolCallId'), '')})
            elif kind == 'usage_update':
                context['used'].append(int(update.get('used') or 0))   # 每次请求一条：这次请求的上下文大小

        conn = None
        try:
            conn = self._ensure(selected, target)
            conn.listeners.append(listen)
            session = conn.request('session/new', {'cwd': str(cwd), 'mcpServers': self.mcp_servers}, 60)
            self.session_id = session.get('sessionId')
            if not self.session_id: raise RuntimeError('DSH 没有返回会话')
            wanted = json.dumps([route, model], separators=(',', ':'))
            option = next((o for o in session.get('configOptions') or [] if o.get('id') == 'model'), None)
            if option and option.get('currentValue') != wanted and model:
                try: conn.request('session/set_config_option', {'sessionId': self.session_id, 'configId': 'model', 'value': wanted}, 30)
                except Exception as error:
                    # 不认识这个路由或模型时不退回 DSH 默认模型（那会用另一家的 Key 计费），直接报错
                    scope = f'（{target.source}）' if target is not None and getattr(target, 'source', '') else ''
                    raise RuntimeError(f'Harness 不支持办事模型 {model}{scope}。请到设置 → 模型，在对应的设置范围里把“办事”'
                                       f'换成 Harness 支持的模型') from error
            if self.stopping: raise InterruptedError
            reply = conn.request('session/prompt', {'sessionId': self.session_id, 'prompt': [{'type': 'text', 'text': prompt}]}, PROMPT_TIMEOUT)
            reason = reply.get('stopReason') or 'end_turn'
        except InterruptedError:
            reason = 'cancelled'
        except Exception as error:
            if self.stopping: reason = 'cancelled'
            else:
                stderr = '\n'.join(conn.stderr[-15:]) if conn else ''
                self.close()   # 运行时可能已经坏了，下次重新启动
                self.done.set()
                return DshResult('failed', f'出错了：{str(error)[:300]}', steps, self._final_usage(on_event, context), stderr[:8000])
        finally:
            if conn is not None and listen in conn.listeners: conn.listeners.remove(listen)
        usage = self._final_usage(on_event, context)
        self.done.set()
        status = {'end_turn': 'done', 'cancelled': 'cancelled', 'max_tokens': 'limit', 'max_turn_requests': 'limit'}.get(reason, 'failed')
        reply_text = ''.join(text[context['final_from']:]).strip() or ''.join(text).strip()
        if status == 'cancelled': reply_text = '好，我停下了。'   # 停下时的半句话是过程，不当回复
        elif not reply_text: reply_text = '做完了。' if status == 'done' else f'没有做完（{reason}）。'
        return DshResult(status, reply_text, steps, usage, '' if status in ('done', 'cancelled') else '\n'.join((conn.stderr if conn else [])[-15:]))

    def _final_usage(self, on_event, context) -> Usage:
        """优先用 DSH 记的准确用量；它的缓存文件常常还没写到最新（实测），这时按每次请求的上下文大小估算：
        输入 = 各次请求上下文之和，缓存命中 ≈ 上一次请求的上下文（同一对话前缀），输出 ≈ 回复和工具参数字数 / 1.6。"""
        used = context['used']
        estimate = Usage(sum(used), int(context['chars']/1.6), sum(used[:-1]))
        exact = self._usage(self.session_id) if self.session_id else None
        usage = exact if exact is not None and exact.prompt >= 0.7*estimate.prompt else estimate
        if usage.total: on_event('usage', {'prompt': usage.prompt, 'completion': usage.completion, 'cached': usage.cached, 'estimated': usage is estimate})
        return usage
