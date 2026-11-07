"""她的"手"：改文件、删文件、跑命令都走这里（MCP，HTTP 跑在本进程里），像 Claude Code / Codex 一样在执行前把关。

DeepSeek Harness 只负责想和调度；它自带的写文件、PowerShell、搜文件工具在补丁里关掉了（Windows 上它没有可用的沙箱和审批通道，
exFAT 盘上更是没有任何权限控制——实测）。所以真正动手的这些工具在这里执行，规则是：

- 读：随便读，除了密钥 / 凭据 / 我的数据库 / 系统凭据目录（一律拒绝，不问）；
- 写、改、建文件夹：在"放开的文件夹"（任务原话里点名的文件夹 + 工作区）里直接做，覆盖前把旧版本存进回收区；
  在外面先问你；
- 删除、移走：总是先问（她这次自己新建的文件除外），而且不是真删，是移到回收区，可以找回；
- 命令：只读的命令（Get-ChildItem、Select-String、git status……）直接跑，其他的先把命令给你看再跑；
  命令以 Windows 低完整性级别运行，C 盘的系统和用户目录它写不进去。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

from aicompanion.agent.paths import protected_reason

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
READ_ONLY_GIT = {'status', 'log', 'diff', 'show', 'branch', 'remote', 'rev-parse', 'ls-files', 'blame'}
DANGER_HINT = re.compile(r'(?i)\b(remove-item|rm|del|erase|rd|rmdir|format|clear-content|set-content|out-file|add-content|new-item|'
                         r'move-item|mv|copy-item|cp|rename-item|ren|stop-process|kill|invoke-webrequest|iwr|curl|wget|start-process|'
                         r'set-executionpolicy|reg|shutdown|restart-computer|git\s+(push|reset|clean|checkout|commit)|npm\s+(publish|install|i)|'
                         r'hexo\s+(deploy|d|clean)|pip\s+install)\b|>{1,2}')


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
    for method in re.findall(r'(?:\.|::)(\w+)\s*\(', text):
        if method.lower() not in SAFE_METHODS: return False
    for part in _segments(text):
        if re.match(r'^\$[\w:.]+\s*=[^=]', part): part = part.split('=', 1)[1].strip()   # $x = ...：看等号右边
        words = part.split()
        while words and words[0] in ('&', '.', '('): words = words[1:]   # & 命令 / . 脚本 都按后面的命令算
        raw = words[0].lstrip('&(') if words else ''
        if not raw or raw[0] in '$"\'@[0123456789-+' or raw.lower() in ('if', 'else', 'elseif', 'foreach', 'for', 'while'):
            continue   # 变量、字面量、表达式、比较、流程控制（括号里的内容已经单独检查过）
        head = re.split(r'[()]', raw)[0].lower()
        words = [head] + words[1:]
        if head == 'git' and len(words) > 1 and words[1].lower() in READ_ONLY_GIT: continue
        if ' '.join(w.lower() for w in words[:2]) in READ_ONLY or head in READ_ONLY: continue
        return False
    return True


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
    created: set = field(default_factory=set)              # 这次任务新建的文件（删它不用问）
    always: set = field(default_factory=set)               # 选了"本次任务都可以"的类别（'command' 或 文件夹路径）
    cancelled: threading.Event = field(default_factory=threading.Event)


class ToolBox:
    """工具实现 + 把关。approve(kind, title, detail) -> 'once' / 'always' / 'no'，在后台线程里阻塞调用。"""

    def __init__(self, secrets: list[Path] | None = None, run_command: Callable | None = None, trash: Path | None = None):
        self.secrets = [_norm(s) for s in (secrets or [])]
        self.trash = trash        # 测试时指定；平时按盘选（见 trash_root）
        self.approve: Callable[[str, str, str], str] = lambda kind, title, detail: 'no'
        self.on_call: Callable[[dict], None] = lambda record: None
        self.task = Task()
        self.run_command = run_command or self._run_powershell

    # ---- MCP 工具清单 ----------------------------------------------------------------
    TOOLS = [
        {'name': 'list_dir', 'description': '列出文件夹里的文件和子文件夹（名字、大小、修改时间）。',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'description': '绝对路径'}}, 'required': ['path']}},
        {'name': 'glob', 'description': '按通配符找文件，例如 **/*.md。返回绝对路径列表（最多 500 个）。',
         'inputSchema': {'type': 'object', 'properties': {'pattern': {'type': 'string'}, 'path': {'type': 'string', 'description': '在哪个文件夹下找'}}, 'required': ['pattern', 'path']}},
        {'name': 'grep', 'description': '在文件内容里搜正则，返回 文件:行号:内容（最多 300 行）。',
         'inputSchema': {'type': 'object', 'properties': {'pattern': {'type': 'string'}, 'path': {'type': 'string'}, 'glob': {'type': 'string', 'description': '只搜这些文件，如 *.md'}}, 'required': ['pattern', 'path']}},
        {'name': 'read_file', 'description': '读文本文件。大文件用 offset / limit 按行分段读。',
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
    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """执行一个工具，返回（文字结果, 是否出错）。"""
        record = {'tool': name, 'args': args, 'decision': 'auto', 'result': '', 'error': ''}
        try:
            if self.task.cancelled.is_set(): raise PermissionError('任务已经停止')
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
            try: self.on_call(record)
            except Exception: pass

    # ---- 把关 ------------------------------------------------------------------------
    def _check_read(self, path: Path):
        reason = _secret_reason(path, self.secrets)
        if reason: raise PermissionError(f'{path} 不能读（{reason}）')

    def _allowed(self, path: Path) -> bool:
        return any(_inside(path, folder) or path == folder for folder in self.task.allowed+[Path(p) for p in self.task.always if p != 'command'])

    def _ask(self, record, kind: str, title: str, detail: str, remember: str | None = None):
        if remember and remember in self.task.always:
            record['decision'] = 'approved_session'; return
        answer = self.approve(kind, title, detail)
        if self.task.cancelled.is_set(): raise PermissionError('任务已经停止')
        if answer == 'always' and remember: self.task.always.add(remember)
        if answer in ('once', 'always'):
            record['decision'] = 'approved_session' if answer == 'always' else 'approved'; return
        record['decision'] = 'denied' if answer == 'no' else 'timeout'
        raise PermissionError('用户没有同意' if answer == 'no' else '用户没有回应')

    def _check_write(self, record, path: Path, action: str):
        reason = _secret_reason(path, self.secrets)
        if reason: raise PermissionError(f'{path} 不能改（{reason}）')
        if self._allowed(path): return
        folder = path if path.is_dir() else path.parent
        self._ask(record, 'write', f'要在放开的文件夹外{action}：\n{path}',
                  f'这次任务放开的文件夹：\n' + '\n'.join(str(p) for p in self.task.allowed) + f'\n\n同意后她会{action}这个位置；选"这个文件夹本次都可以"会放开 {folder}。',
                  remember=str(folder))

    def _backup(self, path: Path) -> str:
        """覆盖或删除前把旧版本移 / 复制进回收区：<盘>\\.aicompanion-trash\\<时间>-<任务号>\\<原路径>。"""
        if not path.exists(): return ''
        stamp = time.strftime('%Y%m%d-%H%M%S')
        target = (self.trash or trash_root(path))/f'{stamp}-run{self.task.run_id}'/str(path)[len(path.drive):].lstrip('\\/')
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
                if not _secret_reason(path, self.secrets): yield path

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
        root = _norm(path); self._check_read(root)
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
        p = _norm(path); self._check_read(p)
        text = p.read_text(encoding='utf-8', errors='replace')
        lines = text.splitlines(); start = max(1, int(offset or 1))
        chosen = lines[start-1:start-1+int(limit)] if limit else lines[start-1:]
        out = '\n'.join(f'{start+i}\t{line}' for i, line in enumerate(chosen))
        if len(out) > READ_LIMIT: out = out[:READ_LIMIT]+f'\n（太长，只显示前 {READ_LIMIT} 字；用 offset / limit 分段读，共 {len(lines)} 行）'
        return out or '（空文件）'

    # ---- 写 --------------------------------------------------------------------------
    def _t_write_file(self, record, path, content):
        p = _norm(path); self._check_write(record, p, '写入文件')
        backup = self._backup(p) if p.exists() else ''
        if not p.exists(): self.task.created.add(str(p).lower())
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding='utf-8')
        return f'已写入 {p}（{len(content)} 字）'+(f'；旧版本留底在 {backup}' if backup else '')

    def _t_edit_file(self, record, path, old_text, new_text, replace_all=False):
        p = _norm(path); self._check_write(record, p, '修改文件')
        text = p.read_text(encoding='utf-8')
        count = text.count(old_text)
        if count == 0: raise ValueError('old_text 在文件里找不到（要和原文一字不差，包括空格和换行）')
        if count > 1 and not replace_all: raise ValueError(f'old_text 出现了 {count} 次，请多带一点上下文让它唯一，或设 replace_all')
        backup = self._backup(p)
        p.write_text(text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1), encoding='utf-8')
        return f'已修改 {p}（替换 {count if replace_all else 1} 处）；旧版本留底在 {backup}'

    def _t_make_dir(self, record, path):
        p = _norm(path); self._check_write(record, p, '新建文件夹')
        p.mkdir(parents=True, exist_ok=True); self.task.created.add(str(p).lower())
        return f'已建好 {p}'

    def _t_move_path(self, record, source, destination):
        src, dst = _norm(source), _norm(destination)
        for p in (src, dst):
            reason = _secret_reason(p, self.secrets)
            if reason: raise PermissionError(f'{p} 不能动（{reason}）')
        if not (self._allowed(src) and self._allowed(dst)) and str(src).lower() not in self.task.created:
            self._ask(record, 'move', f'要把\n{src}\n移到\n{dst}', '移动后原位置就没有这个文件了（原文件会先留底到回收区）。')
        backup = self._backup(src)
        dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(dst))
        return f'已移动到 {dst}'+(f'；原样留底在 {backup}' if backup else '')

    def _t_delete_path(self, record, path, reason=''):
        p = _norm(path)
        secret = _secret_reason(p, self.secrets)
        if secret: raise PermissionError(f'{p} 不能删（{secret}）')
        if not p.exists(): return f'{p} 本来就不存在'
        if str(p).lower() not in self.task.created:
            what = '整个文件夹' if p.is_dir() else '文件'
            self._ask(record, 'delete', f'要删除{what}：\n{p}'+(f'\n原因：{reason}' if reason else ''),
                      '同意后会移到同一个盘的 .aicompanion-trash 回收区，需要时可以从那里找回。')
        target = self._backup(p)
        if p.is_dir(): shutil.rmtree(p)
        else: p.unlink()
        return f'已删除 {p}（移到了回收区 {target}）'

    # ---- 命令 ------------------------------------------------------------------------
    def _t_run_powershell(self, record, command, cwd=None, reason=''):
        folder = _norm(cwd) if cwd else (self.task.allowed[0] if self.task.allowed else Path.home())
        for p in [folder, *self._paths_in(command)]:
            secret = _secret_reason(p, self.secrets)
            if secret: raise PermissionError(f'命令涉及 {p}（{secret}）')
        names = {s.name.lower() for s in self.secrets if s.suffix or s.name.startswith('.')} | {'.env', 'id_rsa', 'id_ed25519', '.git-credentials'}
        if any(re.search(r'(?<![\w.-])'+re.escape(n)+r'(?![\w-])', command.lower()) for n in names) or re.search(r'(?i)aicompanion[^\s]*\.sqlite3', command):
            raise PermissionError('命令涉及密钥文件或我的数据库')
        if not is_read_only_command(command):
            self._ask(record, 'command', '要运行命令：\n'+command[:600]+(f'\n用途：{reason}' if reason else ''),
                      f'在 {folder} 运行。这条命令可能会改动文件或系统，请看清楚再同意。命令以低权限运行，改不了 C 盘的系统和用户目录。',
                      remember='command')
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
        exe = os.path.join(os.getenv('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        script = '[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference="SilentlyContinue"; '+command
        args = [exe, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', script]
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

        def launch():
            return subprocess.run(args, cwd=str(folder), env=env, capture_output=True, timeout=COMMAND_TIMEOUT, creationflags=flags)
        try:
            if sandbox.IS_WINDOWS:
                with sandbox.low_integrity(): done = launch()
            else:
                done = launch()
        except subprocess.TimeoutExpired:
            return f'命令超过 {COMMAND_TIMEOUT} 秒被停止了。长时间运行的程序请用 Start-Process 放到后台。'
        out = (done.stdout or b'').decode('utf-8', 'replace'); err = (done.stderr or b'').decode('utf-8', 'replace')
        text = out+(('\n[stderr]\n'+err) if err.strip() else '')+f'\n[退出码 {done.returncode}]'
        return text[-OUTPUT_LIMIT:] if len(text) > OUTPUT_LIMIT else text
