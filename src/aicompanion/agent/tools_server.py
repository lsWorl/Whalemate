"""她的"手"：改文件、删文件、跑命令都走这里（MCP，HTTP 跑在本进程里），像 Claude Code / Codex 一样在执行前把关。

DeepSeek Harness 只负责想和调度；它自带的写文件、PowerShell、搜文件工具在补丁里关掉了（Windows 上它没有可用的沙箱和审批通道，
exFAT 盘上更是没有任何权限控制——实测）。所以真正动手的这些工具在这里执行，规则是：

- 读：随便读，除了密钥 / 凭据 / 我的数据库 / 系统凭据目录（一律拒绝，不问）；
- 写入、移动、命令按本次任务的模式和项目规则审批，拒绝规则优先；覆盖前备份；
- 删除走回收区，继续任务的副作用、附件原件、越界写入等必须逐项确认；
- Windows 命令以低完整性级别运行。命令前缀规则仅适用于可辨认的单一命令。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import threading
import time
from typing import Callable

from aicompanion.agent.paths import protected_reason
from aicompanion.agent.approval import MODES, PROJECT_CODE, Rule, Rules, denied_command_family, command_prefix, system_location

TRASH_NAME = '.aicompanion-trash'
READ_LIMIT = 200_000
OUTPUT_LIMIT = 30_000
COMMAND_TIMEOUT = 120
SKIP_DIRS = {'node_modules', '.git', '.deploy_git', '__pycache__', '.venv', 'venv', TRASH_NAME}

# 只读命令：整条命令里每一段都在这个名单里才算只读（不认识的一律当作会改东西）
READ_ONLY = {
    'get-childitem', 'gci', 'dir', 'ls', 'get-content', 'gc', 'cat', 'type', 'get-item', 'gi', 'get-itemproperty', 'test-path',
    'resolve-path', 'split-path', 'join-path', 'select-string', 'sls', 'measure-object', 'measure', 'select-object', 'select',
    'sort-object', 'sort', 'group-object', 'group', 'where-object', 'where', '?', 'format-table', 'ft', 'format-list', 'fl',
    'format-wide', 'out-string', 'get-filehash', 'get-date', 'get-location', 'pwd', 'whoami', 'hostname', 'get-command', 'gcm',
    'get-help', 'get-process', 'ps', 'get-service', 'get-computerinfo', 'get-psdrive', 'get-volume', 'convertto-json', 'convertfrom-json',
    'write-output', 'echo', 'write-host', 'compare-object', 'get-unique', 'tree', 'findstr', 'where.exe', 'systeminfo',
    'python --version', 'python -v', 'node --version', 'node -v', 'npm --version', 'git --version',
}
READ_ONLY_GIT = {'status', 'rev-parse', 'ls-files'}
# 会显示差异的子命令：仓库配置里的 diff.external / textconv 会让它们启动外部程序，所以只有明确带上关闭开关时才算只读
GIT_DIFFING = {'diff': ('--no-ext-diff', '--no-textconv'), 'show': ('--no-ext-diff', '--no-textconv'), 'blame': ('--no-textconv',)}
# 免确认时每个子命令允许的选项：**完整拼写、逐个精确匹配**。git 接受长选项的唯一前缀缩写（--ext-di = --ext-diff），
# 所以不能用"包含 / 前缀"判断危险选项；名单外的（包括任何缩写）一律转确认。'--' 之后都是路径，不再当选项看。
_DIFF_VIEW = (r'--stat|--numstat|--shortstat|--name-only|--name-status|--summary|--color|--no-color|--word-diff|--color-words|-w|-b|'
              r'--ignore-all-space|--ignore-space-change|--ignore-blank-lines|--minimal|--patience|--histogram|-R|-U\d+|--unified=\d+|'
              r'--no-renames|-M|--find-renames|--check|--exit-code|--quiet|--relative|--no-prefix|--abbrev(=\d+)?')
GIT_OPTIONS = {
    'status': re.compile(r'^(-s|--short|-b|--branch|--porcelain(=v[12])?|--long|-u(no|normal|all)?|--untracked-files=(no|normal|all)|--ignored|--no-renames)$'),
    'rev-parse': re.compile(r'^(--show-toplevel|--abbrev-ref|--short(=\d+)?|--verify|--git-dir|--is-inside-work-tree|--show-prefix|-q|--quiet|--symbolic-full-name)$'),
    'ls-files': re.compile(r'^(-c|--cached|-m|--modified|-o|--others|-d|--deleted|-i|--ignored|--exclude-standard|-s|--stage|-z|--full-name)$'),
    'diff': re.compile(r'^(--no-ext-diff|--no-textconv|--cached|--staged|--no-index|' + _DIFF_VIEW + r')$'),
    'show': re.compile(r'^(--no-ext-diff|--no-textconv|--oneline|-s|--no-patch|--abbrev-commit|--(format|pretty)=[^%]*(%[^G][^%]*)*|' + _DIFF_VIEW + r')$'),
    'blame': re.compile(r'^(--no-textconv|-L|-L\S+|-w|-e|-s|-l|-t|-M|-C|--porcelain|--line-porcelain|--date=[\w-]+|--show-email|--root)$'),
    'log': re.compile(r'^(--oneline|--graph|--decorate(=\w+)?|--no-decorate|--all|--reverse|--first-parent|--no-merges|--merges|'
                      r'--name-only|--name-status|--abbrev-commit|--date=[\w-]+|-n|-\d+|--max-count=\d+|--skip=\d+|'
                      r'--(format|pretty)=[^%]*(%[^G][^%]*)*|--(since|until|after|before|author|committer|grep)=.*)$'),
}
# 每条命令运行时都带上的 git 覆盖：关掉文件系统监视钩子、签名校验、分页程序（这些都会执行仓库配置里写的程序，且 status / log 本身就会触发）。
# diff.external 不在这里覆盖（置空会让用户同意过的 git diff 报错）；只读判断要求 diff / show 带 --no-ext-diff --no-textconv。
GIT_SAFE_CONFIG = (('core.fsmonitor', 'false'), ('log.showSignature', 'false'), ('core.pager', 'cat'))
READ_ONLY_GIT_LIST = {'branch': {'-a', '-r', '-v', '-vv', '--list', '--all', '--remotes', '--show-current'}, 'remote': {'-v', '--verbose'}}
# 会改东西的命令名：出现在命令里的任何位置（包括被藏在字符串、脚本块里）都不算只读。
# 前后不能紧挨字母、连字符、点、路径分隔符，避免 Format-Table、D:\\del\\ 这类误判。
DANGER_HINT = re.compile(r'(?i)(?<![\w\-.\\/:$])(remove-item|rm|del|erase|rd|rmdir|format|clear-content|set-content|out-file|add-content|'
                         r'new-item|ni|md|mkdir|move-item|mv|move|copy-item|cp|copy|rename-item|ren|stop-process|kill|invoke-webrequest|iwr|'
                         r'invoke-restmethod|irm|curl|wget|start-process|saps|start|set-executionpolicy|reg|shutdown|restart-computer|'
                         r'set-item|si|set-itemproperty|new-itemproperty|remove-itemproperty)(?![\w\-.\\/:])'
                         r'|\bgit\s+(push|reset|clean|checkout|commit|branch\s+-[dDmM]|remote\s+(add|remove|rm|set-url))'
                         r'|\b(npm|pnpm|yarn|pip|npx)\s+\w')


SAFE_METHODS = {'tostring', 'trim', 'trimend', 'trimstart', 'split', 'replace', 'substring', 'tolower', 'toupper', 'contains', 'startswith',
                'endswith', 'join', 'format', 'gettype', 'indexof', 'padleft', 'padright', 'equals', 'getfilename', 'getextension', 'combine',
                'readalltext', 'readalllines', 'exists', 'getfiles', 'getdirectories', 'getfilenamewithoutextension', 'getdirectoryname', 'round', 'max', 'min'}


def _segments(text: str) -> list[str]:
    """按 ; | && || 换行切开，并把 (...) $(...) {...} 里面的内容也拿出来单独检查（防止藏在括号或脚本块里的命令）。"""
    parts = [text]; inner = text
    for _ in range(10):
        found = re.findall(r'[({]([^(){}]*)[)}]', inner)
        if not found: break
        parts += found; inner = re.sub(r'[({][^(){}]*[)}]', ' 0 ', inner)
    parts.append(inner)
    out = []
    for part in parts:
        out += [seg.strip() for seg in re.split(r'\|\||&&|[;|\n]', part) if seg.strip()]
    return out


def is_read_only_command(command: str) -> bool:
    """每一段的开头都是只读命令（或变量、字面量、表达式），没有重定向写文件，也没有调用会改东西的 .NET 方法。"""
    text = command.strip()
    if not text or re.search(r'(?<![<>=!-])>{1,2}', text) or '`' in text: return False
    if DANGER_HINT.search(text) or re.search(r'(?i)--output|-outfile|tee-object|invoke-expression|\biex\b|-encodedcommand', text): return False
    for method in re.findall(r'(?:\.|::)(\w+)\s*\(', text):
        if method.lower() not in SAFE_METHODS: return False
    for part in _segments(text):
        assign = re.match(r'^\$\{?([\w:.]+)\}?\s*[-+*/]?=[^=]', part)
        if assign:
            if ':' in assign.group(1) and not assign.group(1).lower().startswith(('script:', 'local:', 'global:', 'private:')):
                return False   # $env:X = …、$function:… 这类是写环境变量 / 提供程序，不是普通变量（会改变后面命令的行为）
            part = part.split('=', 1)[1].strip()   # $x = ...：看等号右边
        words = part.split()
        invoked = bool(words) and (words[0] in ('&', '.') or (words[0][:1] in '&.' and len(words[0]) > 1 and words[0][1] in '$"\'('))
        while words and words[0] in ('&', '.', '('): words = words[1:]   # & 命令 / . 脚本 都按后面的命令算
        raw = words[0].lstrip('&.(') if words else ''
        if invoked and (not raw or raw[0] in '$"\'@[('):
            return False   # & $变量 / & "字符串" / .(…)：调用的是什么命令看不出来，一律当作会改东西
        if not raw or raw[0] in '$"\'@[0123456789-+' or raw.lower() in ('if', 'else', 'elseif', 'foreach', 'for', 'while'):
            continue   # 变量、字面量、表达式、比较、流程控制（括号里的内容已经单独检查过）
        head = re.split(r'[()]', raw)[0].lower()
        words = [head] + words[1:]
        if head in ('git', 'git.exe') and len(words) > 1:
            if _git_read_only(part): continue
            return False
        if ' '.join(w.lower() for w in words[:2]) in READ_ONLY or head in READ_ONLY: continue
        return False
    return True


def _git_read_only(part: str) -> bool:
    """一段 git 命令是否免确认。参数按 PowerShell 传给外部程序的样子拆开：变量、数组、子表达式、splat 展开后是什么看不出来，
    一律转确认；选项必须在子命令的完整名单里（精确匹配，缩写不认），diff/show/blame 还必须带上关闭外部程序的开关。"""
    tokens = re.findall(r"'[^']*'|\"[^\"]*\"|\S+", part)
    if len(tokens) < 2: return False
    args = []
    for token in tokens[1:]:
        if token[0] == "'": args.append(token[1:-1]); continue                 # 单引号：原样，不展开
        if '$' in token or token[0] in '@(' or (token[0] != '"' and (',' in token or '(' in token)) or token == '--%':
            return False                                                         # $变量、@splat、(子表达式)、a,b 数组、--% 停止解析
        args.append(token.strip('"'))
    sub, rest = args[0].lower(), args[1:]
    if sub.startswith('-'): return False                                         # git -c / --exec-path 等全局选项
    if sub in READ_ONLY_GIT_LIST:
        return all(w.lower() in READ_ONLY_GIT_LIST[sub] for w in rest)          # git branch -D、git remote add 不算
    if sub not in GIT_OPTIONS: return False
    options = []
    for i, w in enumerate(rest):
        if w == '--': break                                                      # 之后都是路径
        if w.startswith('-'): options.append(w)
    if not all(GIT_OPTIONS[sub].match(w) for w in options): return False
    if sub in GIT_DIFFING:
        # 关闭开关必须在，而且名单里没有能重新打开它们的选项（--ext-diff / --textconv 及其缩写都不在名单里）
        return all(flag in options for flag in GIT_DIFFING[sub])
    return True


def git_safe_env(env: dict) -> dict:
    """git 的命令行级配置覆盖（GIT_CONFIG_COUNT/KEY/VALUE，优先级高于仓库配置），并清掉会指定外部程序的环境变量。"""
    for key in [k for k in env if k.upper() in ('GIT_EXTERNAL_DIFF', 'GIT_PAGER', 'GIT_SSH_COMMAND', 'GIT_CONFIG_PARAMETERS')]: env.pop(key)
    base = int(env.get('GIT_CONFIG_COUNT') or 0) if str(env.get('GIT_CONFIG_COUNT') or '0').isdigit() else 0
    out = {'GIT_CONFIG_COUNT': str(base+len(GIT_SAFE_CONFIG))}
    for i, (key, value) in enumerate(GIT_SAFE_CONFIG, base):
        out[f'GIT_CONFIG_KEY_{i}'] = key; out[f'GIT_CONFIG_VALUE_{i}'] = value
    return out


def _norm(path: str | Path) -> Path:
    raw = str(path).strip().strip('"\'')
    p = Path(raw)
    try: return p.resolve()
    except OSError: return p.absolute()


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder); return True
    except ValueError:
        return str(path).lower().startswith(str(folder).lower().rstrip('\\/')+os.sep)


def _secret_reason(path: Path, secrets: list[Path]) -> str:
    reason = protected_reason(path)
    if reason: return reason
    for s in secrets:
        try:
            if path == s or s in path.parents or (s.is_dir() and _inside(path, s)): return '这是我的私密数据（密钥 / 数据库）'
        except OSError:
            continue
    if TRASH_NAME in path.parts: return '这是回收区'
    return ''


def trash_root(path: Path) -> Path:
    """回收区：和文件同一个盘（移动快）。C 盘根目录普通用户写不了，放到 %LOCALAPPDATA%\\AICompanion\\trash。"""
    local = Path(os.getenv('LOCALAPPDATA') or Path.home())/'AICompanion'/'trash'
    drive = Path(path).drive
    if not drive or drive.lower() == local.drive.lower(): return local
    return Path(drive+'\\')/TRASH_NAME


@dataclass
class Task:
    """一次任务的放行范围。"""
    run_id: int = 0
    allowed: list[Path] = field(default_factory=list)      # 放开写的文件夹
    always: set[Rule] = field(default_factory=set)          # 仅在本次运行中复用
    cancelled: threading.Event = field(default_factory=threading.Event)
    mode: str = 'workspace'
    rules: Rules | None = None
    peer: tuple | None = None
    resuming: bool = False
    originals: dict[Path, Path] = field(default_factory=dict)  # 原件 → 本任务副本
    copies: set[Path] = field(default_factory=set)
    captions: dict[Path, str] = field(default_factory=dict)
    read_chars: int = 3600


class ToolBox:
    """工具实现 + 把关。approve(kind, title, detail) -> 'once' / 'always' / 'no'，在后台线程里阻塞调用。"""

    def __init__(self, secrets: list[Path] | None = None, run_command: Callable | None = None, trash: Path | None = None):
        self.secrets = [_norm(s) for s in (secrets or [])]
        self.trash = trash        # 测试时指定；平时按盘选（见 trash_root）
        self.approve: Callable[[str, str, str], str] = lambda kind, title, detail: 'no'
        self.on_call: Callable[[dict], None] = lambda record: None
        self.reserve_effect = None
        self.request_approval = None
        self.peer_valid = None
        self.task = Task()                 # 当前任务；每次工具调用开始时绑定到这个线程（见 current）
        self._local = threading.local()
        self._calls_lock = threading.Lock()
        self._active_calls = 0
        self.run_command = run_command or self._run_powershell

    # ---- MCP 工具清单 ----------------------------------------------------------------
    TOOLS = [
        {'name': 'list_dir', 'description': '列出文件夹里的文件和子文件夹（名字、大小、修改时间）。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': '绝对路径'}}, 'required': ['path']}},
        {'name': 'glob', 'description': '按通配符找文件，例如 **/*.md。返回绝对路径列表（最多 500 个）。',
         'inputSchema': {'type': 'object', 'properties': {'pattern': {'type': 'string'}, 'path': {'type': 'string', 'description': '在哪个文件夹下找'}}, 'required': ['pattern', 'path']}},
        {'name': 'grep', 'description': '在文件内容里搜正则，返回 文件:行号:内容（最多 300 行）。',
         'inputSchema': {'type': 'object', 'properties': {'pattern': {'type': 'string'}, 'path': {'type': 'string'}, 'glob': {'type': 'string', 'description': '只搜这些文件，如 *.md'}}, 'required': ['pattern', 'path']}},
        {'name': 'read_file', 'description': '读文本文件和本任务的文档附件、图片识别结果。大文件用 offset / limit 按行分段读。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'offset': {'type': 'integer', 'description': '从第几行开始（1 起）'}, 'limit': {'type': 'integer', 'description': '读多少行'}}, 'required': ['path']}},
        {'name': 'write_file', 'description': '写文本文件（新建或整个覆盖，UTF-8）。覆盖前旧版本会自动留底。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']}},
        {'name': 'edit_file', 'description': '把文件里的一段原文替换成新内容。old_text 必须和原文一字不差且只出现一次（replace_all 为 true 时全部替换）。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}, 'replace_all': {'type': 'boolean'}}, 'required': ['path', 'old_text', 'new_text']}},
        {'name': 'make_dir', 'description': '新建文件夹（含上级）。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}},
        {'name': 'move_path', 'description': '移动或重命名文件 / 文件夹。',
         'inputSchema': {'type': 'object', 'properties': {'source': {'type': 'string'}, 'destination': {'type': 'string'}}, 'required': ['source', 'destination']}},
        {'name': 'delete_path', 'description': '删除文件或文件夹：会先问用户，同意后移到回收区（可找回）。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'reason': {'type': 'string', 'description': '一句话说明为什么要删'}}, 'required': ['path']}},
        {'name': 'run_powershell', 'description': '运行 PowerShell 命令并返回输出。只读命令直接运行，会改东西的命令先给用户看、同意后才运行。'
                                                  '要长时间运行的程序（如本地预览服务器）请用 Start-Process 放到后台。单条命令最长 120 秒。',
         'inputSchema': {'type': 'object', 'properties': {'command': {'type': 'string'}, 'cwd': {'type': 'string', 'description': '在哪个文件夹运行（默认工作区）'},
                                                          'reason': {'type': 'string', 'description': '一句话说明这条命令要做什么'}}, 'required': ['command']}},
    ]

    # ---- 调度 ------------------------------------------------------------------------
    @property
    def working(self) -> bool:
        with self._calls_lock:
            return self._active_calls > 0

    @property
    def current(self) -> Task:
        """这次调用所属的任务。调用开始时绑定，之后即使 self.task 换成了新任务，这次调用也只认原来那个
        （旧命令照样会被旧任务的停止信号结束，迟到的审批和审计也记在原任务名下）。"""
        return getattr(self._local, 'task', None) or self.task

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """执行一个工具，返回（文字结果, 是否出错）。"""
        task = self.task; self._local.task = task
        with self._calls_lock:
            self._active_calls += 1
        record = {'tool': name, 'args': args, 'decision': 'auto', 'result': '', 'error': '', 'run_id': task.run_id}
        try:
            if task.cancelled.is_set(): raise PermissionError('任务已经停止')
            if task.peer and self.peer_valid and not self.peer_valid(task.peer):
                raise PermissionError('主人 QQ 绑定已失效')
            handler = getattr(self, f'_t_{name}', None)
            if handler is None: raise ValueError(f'没有这个工具：{name}')
            text = handler(record, **{k: v for k, v in (args or {}).items() if v is not None})
            record['result'] = text[:300]
            return text, False
        except PermissionError as error:
            record['error'] = str(error); record['decision'] = record['decision'] if record['decision'] != 'auto' else 'denied'
            return f'被拒绝：{error}。不要换个办法绕过去，把情况告诉用户。', True
        except Exception as error:
            record['error'] = f'{type(error).__name__}: {error}'
            return f'出错：{type(error).__name__}: {error}', True
        finally:
            self._local.task = None
            try: self.on_call(record)
            except Exception: pass
            finally:
                with self._calls_lock:
                    self._active_calls -= 1

    # ---- 把关 ------------------------------------------------------------------------
    def _before_effect(self, record, *operation):
        task = self.current
        if task.cancelled.is_set():
            raise PermissionError('任务已经停止')
        if task.peer and self.peer_valid and not self.peer_valid(task.peer):
            raise PermissionError('主人 QQ 绑定已失效')
        if self.reserve_effect and task.run_id:
            # 用实际路径、命令和内容计算指纹，不把文件正文、命令或凭据写入恢复记录。
            payload = json.dumps([record['tool'], *operation], ensure_ascii=False)
            self.reserve_effect(task.run_id, hashlib.sha256(payload.encode()).hexdigest(), task.resuming)

    def _check_read(self, path: Path):
        reason = _secret_reason(path, self.secrets)
        if reason: raise PermissionError(f'{path} 不能读（{reason}）')

    def _allowed(self, path: Path) -> bool:
        return any(path.is_relative_to(folder) for folder in self.current.allowed)

    def _authorize(self, record, kind, title, detail, candidate=None, hard=False, readonly=False, deny_scopes=()):
        task = self.current
        reusable = bool(candidate and not hard)
        hit = task.rules.hit(candidate, reusable, deny_scopes) if task.rules else None
        if hit and hit[0] == 'deny':
            record['decision'] = '拒绝规则 '+hit[1]
            raise PermissionError(record['decision'])
        if reusable and hit:
            record['decision'] = '项目规则 '+hit[1]
            return
        with self._calls_lock:
            matched = next((rule for rule in task.always if reusable and rule.matches(candidate)), None)
        if matched:
            record['decision'] = '任务规则 ' + matched.pattern
            return
        if readonly and not hard:
            record['decision'] = '只读操作'
            return
        mode_ok = task.mode in ('auto', 'workspace') and kind in ('write', 'move')
        if not hard and (mode_ok or task.mode == 'auto' and reusable):
            record['decision'] = '模式 '+MODES[task.mode]
            return
        rule = candidate if reusable else None
        if self.request_approval:
            answer, reason = self.request_approval(kind,title,detail,rule)
        else:
            answer, reason = self.approve(kind,title,detail), ''
        if task.cancelled.is_set():
            raise PermissionError('任务已经停止')
        if answer in ('once','always','persist'):
            if answer == 'always' and rule:
                with self._calls_lock:
                    task.always.add(rule)
            if answer == 'persist' and rule and task.rules:
                task.rules.put(rule, 'allow')
            record['decision'] = 'approved_session' if answer=='always' and rule else 'approved'
            return
        record['decision'] = 'timeout' if answer=='timeout' else 'denied'
        raise PermissionError(('用户没有回应' if answer=='timeout' else '用户没有同意')+('：'+reason if reason else ''))

    def _hard_path(self, path):
        return (not self._allowed(path) or system_location(path) or self._touches_original(path)
                or any(part.casefold()=='.git' for part in path.parts)
                or (path.is_file() and path.stat().st_nlink>1))

    def _touches_original(self, path):
        return any(path == original or path in original.parents for original in self.current.originals)

    def _check_write(self, record, path: Path, action: str):
        reason = _secret_reason(path, self.secrets)
        if reason: raise PermissionError(f'{path} 不能改（{reason}）')
        task = self.current
        kind = 'resume' if task.resuming else 'original' if self._touches_original(path) else 'write'
        folder = path if path.is_dir() else path.parent
        title = f'要{action}'
        details = ['请确认这是还没做的部分。'] if task.resuming else []
        if any(part.lower()=='.git' for part in path.parts):
            details.append('.git 的 config、hooks 可以执行程序。')
        if not self._allowed(path):
            title += '，超出放开范围'
            details.append('放开的文件夹：'+'、'.join(map(str,task.allowed)))
        self._authorize(record,kind,f'{title}：\n{path}', '\n'.join(details), Rule('write', str(folder)),
                        task.resuming or self._hard_path(path))

    def _backup(self, path: Path) -> str:
        """覆盖或删除前把旧版本移 / 复制进回收区：<盘>\\.aicompanion-trash\\<时间>-<任务号>\\<原路径>。"""
        if not path.exists(): return ''
        stamp = time.strftime('%Y%m%d-%H%M%S')
        target = (self.trash or trash_root(path))/f'{stamp}-run{self.current.run_id}'/str(path)[len(path.drive):].lstrip('\\/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir(): shutil.copytree(path, target, dirs_exist_ok=True)
        else: shutil.copy2(path, target)
        return str(target)

    # ---- 读 --------------------------------------------------------------------------
    def _t_list_dir(self, record, path):
        p = _norm(path); self._check_read(p)
        rows = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))[:500]:
            if child.name == TRASH_NAME: continue
            try:
                st = child.stat()
                rows.append(f'{"[目录] " if child.is_dir() else ""}{child.name}\t{"" if child.is_dir() else st.st_size}\t{time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))}')
            except OSError:
                rows.append(child.name)
        return '\n'.join(rows) or '（空文件夹）'

    def _walk(self, root: Path):
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not _secret_reason(Path(base)/d, self.secrets)]
            for name in files:
                path = Path(base)/name
                if path not in self.current.originals and not _secret_reason(path, self.secrets): yield path

    def _t_glob(self, record, pattern, path):
        root = _norm(path); self._check_read(root)
        pattern = pattern.replace('\\', '/')
        out = []
        for p in self._walk(root):
            rel = p.relative_to(root).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern) or (pattern.startswith('**/') and fnmatch.fnmatch(rel, pattern[3:])):
                out.append(str(p))
                if len(out) >= 500: break
        return '\n'.join(out) or '（没找到）'

    def _t_grep(self, record, pattern, path, glob=None):
        root = _norm(path)
        root = self.current.originals.get(root, root)
        self._check_read(root)
        regex = re.compile(pattern); out = []
        files = [root] if root.is_file() else self._walk(root)
        for p in files:
            if glob and not fnmatch.fnmatch(p.name, glob): continue
            try:
                if p.stat().st_size > 5_000_000: continue
                with open(p, encoding='utf-8', errors='ignore') as handle:
                    for number, line in enumerate(handle, 1):
                        if regex.search(line):
                            out.append(f'{p}:{number}:{line.rstrip()[:300]}')
                            if len(out) >= 300: return '\n'.join(out)+'\n（只显示前 300 行）'
            except OSError:
                continue
        return '\n'.join(out) or '（没有匹配）'

    def _t_read_file(self, record, path, offset=1, limit=None):
        requested = self.current.originals.get(_norm(path), Path(path))
        p = _norm(requested)
        self._check_read(p)
        if requested in self.current.copies and p != requested:
            raise PermissionError('附件副本路径被替换，请重新添加')
        if p in self.current.copies:
            from aicompanion.agent.attachment_text import document_text, read_page
            from aicompanion.agent.attachments import read_copy
            text = self.current.captions.get(p)
            if text is None:
                text = document_text(p, read_copy(p))
            return read_page(text, offset, limit, self.current.read_chars)
        text = p.read_text(encoding='utf-8', errors='replace')
        lines = text.splitlines(); start = max(1, int(offset or 1))
        chosen = lines[start-1:start-1+int(limit)] if limit else lines[start-1:]
        out = '\n'.join(f'{start+i}\t{line}' for i, line in enumerate(chosen))
        if len(out) > READ_LIMIT: out = out[:READ_LIMIT]+f'\n（太长，只显示前 {READ_LIMIT} 字；用 offset / limit 分段读，共 {len(lines)} 行）'
        return out or '（空文件）'

    # ---- 写 --------------------------------------------------------------------------
    def _t_write_file(self, record, path, content):
        p = _norm(path); self._check_write(record, p, '写入文件')
        self._before_effect(record, os.path.normcase(p), content)
        backup = self._backup(p) if p.exists() else ''
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding='utf-8')
        return f'已写入 {p}（{len(content)} 字）'+(f'；旧版本留底在 {backup}' if backup else '')

    def _t_edit_file(self, record, path, old_text, new_text, replace_all=False):
        p = _norm(path); self._check_write(record, p, '修改文件')
        text = p.read_text(encoding='utf-8')
        count = text.count(old_text)
        if count == 0: raise ValueError('old_text 在文件里找不到（要和原文一字不差，包括空格和换行）')
        if count > 1 and not replace_all: raise ValueError(f'old_text 出现了 {count} 次，请多带一点上下文让它唯一，或设 replace_all')
        self._before_effect(record, os.path.normcase(p), old_text, new_text, bool(replace_all))
        backup = self._backup(p)
        p.write_text(text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1), encoding='utf-8')
        return f'已修改 {p}（替换 {count if replace_all else 1} 处）；旧版本留底在 {backup}'

    def _t_make_dir(self, record, path):
        p = _norm(path); self._check_write(record, p, '新建文件夹')
        self._before_effect(record, os.path.normcase(p))
        p.mkdir(parents=True, exist_ok=True)
        return f'已建好 {p}'

    def _protected_tree(self, root):
        if not root.is_dir():
            return False
        def unreadable(error):
            raise PermissionError('不能核对目录内容：' + str(error))
        for base, dirs, files in os.walk(root, onerror=unreadable):
            for name in dirs + files:
                path = Path(base) / name
                info = path.lstat()
                if (name.casefold() == '.git' or _secret_reason(path, self.secrets)
                        or path in self.current.originals or path.is_symlink() or info.st_nlink > 1 and path.is_file()
                        or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    return True
        return False

    def _t_move_path(self, record, source, destination):
        src, dst = _norm(source), _norm(destination)
        for p in (src, dst):
            reason = _secret_reason(p, self.secrets)
            if reason: raise PermissionError(f'{p} 不能动（{reason}）')
        if self._protected_tree(src):
            raise PermissionError('目录内有受保护的文件或链接，请逐项移动')
        target = dst/src.name if dst.is_dir() else dst
        target = target.resolve()
        reason = _secret_reason(target, self.secrets)
        if reason:
            raise PermissionError(f'{target} 不能动（{reason}）')
        for location in (src,target):
            if any(location == secret or location in secret.parents for secret in self.secrets):
                raise PermissionError('不能移动包含私密数据的目录')
        try:
            common = Path(os.path.commonpath((src.parent,target.parent)))
        except ValueError:
            common = None
        rule = Rule('move', str(common)) if common and self._allowed(common) else None
        self._authorize(record, 'resume' if self.current.resuming else 'move', f'移动 {src} → {target}',
                        '原位置会留底到回收区。', rule, self.current.resuming or
                        any(self._hard_path(p) for p in (src, target)),
                        deny_scopes=[Rule('move', str(p)) for p in (src, target)])
        self._before_effect(record, os.path.normcase(src), os.path.normcase(dst))
        backup = self._backup(src)
        dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dst))
        return f'已移动到 {dst}'+(f'；原样留底在 {backup}' if backup else '')

    def _t_delete_path(self, record, path, reason=''):
        p = _norm(path)
        secret = _secret_reason(p, self.secrets)
        if secret: raise PermissionError(f'{p} 不能删（{secret}）')
        if any(p == secret or p in secret.parents for secret in self.secrets):
            raise PermissionError('不能删除包含私密数据的目录')
        if not p.exists(): return f'{p} 本来就不存在'
        self._authorize(record,'delete',f'移入回收区：{p}',str(reason),hard=True)
        self._before_effect(record, os.path.normcase(p))
        if self._protected_tree(p):
            raise PermissionError('目录内有受保护的文件或链接，请逐项移入回收区')
        target = self._backup(p)
        if p.is_dir(): shutil.rmtree(p)
        else: p.unlink()
        return f'已删除 {p}（移到了回收区 {target}）'

    # ---- 命令 ------------------------------------------------------------------------
    def _t_run_powershell(self, record, command, cwd=None, reason=''):
        folder = _norm(cwd) if cwd else (self.current.allowed[0] if self.current.allowed else Path.home())
        for p in [folder, *self._paths_in(command)]:
            secret = _secret_reason(p, self.secrets)
            if secret: raise PermissionError(f'命令涉及 {p}（{secret}）')
        names = {s.name.lower() for s in self.secrets if s.suffix or s.name.startswith('.')} | {'.env', 'id_rsa', 'id_ed25519', '.git-credentials'}
        if any(re.search(r'(?<![\w.-])'+re.escape(n)+r'(?![\w-])', command.lower()) for n in names) or re.search(r'(?i)aicompanion[^\s]*\.sqlite3', command):
            raise PermissionError('命令涉及密钥文件或我的数据库')
        read_only = is_read_only_command(command)
        if self.current.originals and read_only:
            for path in self._paths_in(command):
                if path in self.current.originals:
                    raise PermissionError(f'请用 read_file 读取附件副本：{self.current.originals[path]}')
        prefix = command_prefix(command)
        operands = command.split()[len(prefix.split()):] if prefix else []
        paths = [folder, *self._paths_in(command)]
        paths.extend(_norm(folder/token) for token in operands if not token.startswith('-'))
        if prefix == 'copy-item' and len(operands) == 2:
            source, dest = (_norm(folder / arg) for arg in operands)
            if dest.is_dir():
                paths.append((dest / source.name).resolve())
        for p in paths:
            reason = _secret_reason(p,self.secrets)
            if reason: raise PermissionError(f'命令涉及受保护的位置（{reason}）')
        hard = (self.current.resuming or prefix in PROJECT_CODE or prefix == 'git add' or any(self._hard_path(p) for p in paths)
                or prefix == 'copy-item' and bool(operands) and _norm(folder / operands[0]).is_dir()
                or bool(self.current.originals and not read_only))
        # 删除与网络执行不能借命令工具代替专用回收区或规避逐项审批。
        if re.search(r'(?i)(?:^|[;|&\s])(?:rm|ri|rd|rmdir|del|erase|remove-item)(?:\s|$)', command):
            raise PermissionError('删除请用 delete_path，逐项确认后进入回收区')
        kind = 'resume' if self.current.resuming else 'command'
        families = {denied_command_family(part) for part in _segments(command)}
        detail = f'工作目录：{folder}\n用途：{reason}'
        if prefix in PROJECT_CODE:
            detail += '\n这条命令会执行项目自己的脚本，需逐次确认。'
        self._authorize(record,kind,'要运行命令：\n'+command, detail,
                        Rule('command',prefix) if prefix else None,
                        hard if not read_only or self.current.resuming else False, readonly=read_only,
                        deny_scopes=[Rule('command', name) for name in families if name])
        if self.current.resuming or not read_only:
            self._before_effect(record, os.path.normcase(folder), command.strip())
        return self.run_command(command, folder)

    @staticmethod
    def _paths_in(command: str) -> list[Path]:
        from aicompanion.agent.paths import paths_in
        return [_norm(p) for p in paths_in(command)]

    def _run_powershell(self, command: str, folder: Path) -> str:
        from aicompanion.agent import sandbox
        env = sandbox.child_env(os.environ.copy()) if sandbox.IS_WINDOWS else os.environ.copy()
        for key in [k for k in env if k.upper().endswith('_API_KEY') or k.upper() in ('QWEN_API_KEY', 'SPEECH_API_KEY')]:
            env.pop(key, None)   # 命令里拿不到你的密钥
        env.update(git_safe_env(env))
        exe = os.path.join(os.getenv('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        script = '[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference="SilentlyContinue"; '+command
        args = [exe, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', script]
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if sandbox.IS_WINDOWS:
            with sandbox.low_integrity(): return self._run_process(args, folder, env, flags)
        return self._run_process(args, folder, env, flags)

    def _run_process(self, args, folder: Path, env: dict, flags: int = 0, timeout: float = COMMAND_TIMEOUT) -> str:
        """运行命令；任务被停止或超时时连同它启动的子进程一起结束。"""
        task = self.current   # 启动时绑定：轮询期间不再读 self.task
        extra = {} if os.name == 'nt' else {'start_new_session': True}   # 非 Windows：独立进程组，停止时整组结束
        proc = subprocess.Popen(args, cwd=str(folder), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags, **extra)
        out_parts, err_parts = [], []
        readers = [threading.Thread(target=lambda s=stream, b=box: b.append(s.read()), daemon=True)
                   for stream, box in ((proc.stdout, out_parts), (proc.stderr, err_parts))]
        for r in readers: r.start()
        deadline = time.monotonic()+timeout; stopped = ''
        while proc.poll() is None:
            if task.cancelled.is_set(): stopped = '任务已停止，命令也一起停下了。'
            elif time.monotonic() > deadline: stopped = f'命令超过 {int(timeout)} 秒被停止了。长时间运行的程序请用 Start-Process 放到后台。'
            if stopped:
                self._kill_tree(proc); break
            time.sleep(0.1)
        for r in readers: r.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            try: stream.close()
            except OSError: pass
        if stopped: return stopped
        out = b''.join(out_parts).decode('utf-8', 'replace'); err = b''.join(err_parts).decode('utf-8', 'replace')
        text = out+(('\n[stderr]\n'+err) if err.strip() else '')+f'\n[退出码 {proc.returncode}]'
        return text[-OUTPUT_LIMIT:] if len(text) > OUTPUT_LIMIT else text

    @staticmethod
    def _kill_tree(proc):
        """Windows 上用 taskkill /T 连子进程一起结束（PowerShell 里起的程序不会因为父进程被杀而退出）。"""
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
            proc.kill()
        except Exception:
            pass
        try: proc.wait(timeout=5)
        except Exception: pass
