"""执行前授权。持久规则按实际工作区隔离，排队中的许可只在当前进程、当前任务有效。"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import secrets
import threading
import time

DEFAULT_MODE = 'workspace'
MODES = {'ask': '每步确认', 'workspace': '自动接受工作区改动', 'auto': '放手做'}
CATEGORIES = {'write': '写入', 'move': '移动', 'command': '命令'}
# 只有行为可说明的子命令才提供复用候选。脚本入口、解释器 -c、安装和网络执行必须单独看。
PREFIXES = ('git status', 'git diff', 'git log', 'git add', 'python -m pytest', 'pytest',
            'npm run build', 'npm test', 'npm run test', 'cargo test', 'cargo check',
            'set-content', 'add-content', 'new-item', 'copy-item')


def command_family(command):
    """仅辨认命令名，供拒绝规则检查；能否复用许可还要检查整条命令。"""
    tokens = command.strip().lower().split()
    for prefix in PREFIXES:
        parts = prefix.split()
        if tokens[:len(parts)] == parts:
            return prefix
    return ''


def denied_command_family(segment):
    # 调用运算符、带引号程序名、完整路径和可执行文件后缀不能绕过拒绝规则。
    words = re.findall(r"\"[^\"]*\"|'[^']*'|\S+", segment)
    if words and words[0] in ('&', '.'):
        words.pop(0)
    if not words:
        return ''
    words = [word.strip("\"'") for word in words]
    words[0] = re.sub(r'(?i)\.(exe|cmd|bat)$', '', re.split(r'[\\/]', words[0])[-1])
    return command_family(' '.join(words))


def command_prefix(command):
    if not re.fullmatch(r'[a-zA-Z0-9_./:\\ =-]+', command):
        return ''
    prefix = command_family(command)
    if not prefix:
        return ''
    args = command.lower().split()[len(prefix.split()):]
    allowed_flags = {'-q', '-v', '--short', '--stat', '--no-ext-diff', '--no-textconv'}
    if any(arg.startswith('-') and arg not in allowed_flags for arg in args):
        return ''
    if prefix in ('git diff', 'git log') and not {'--no-ext-diff', '--no-textconv'}.issubset(args):
        return ''
    for arg in args:
        if arg.startswith(('/', '\\')) or (':' in arg and not re.fullmatch(r'[a-z]:[\\/][^:]+', arg)):
            return ''
        if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)
               for part in re.split(r'[\\/]', arg)):
            return ''
        if any(part.rstrip(' .') != part and part not in ('.', '..') for part in re.split(r'[\\/]', arg)):
            return ''
    return prefix


# 这些入口执行项目代码（如 conftest.py、prebuild、build.rs）。低完整性令牌无法隔离 exFAT。
PROJECT_CODE = {'python -m pytest', 'pytest', 'npm run build', 'npm test', 'npm run test', 'cargo test', 'cargo check'}


def system_location(path):
    return any(path.is_relative_to(Path(root)) for root in
               (r'C:\Users', r'C:\Windows', r'C:\Program Files', r'C:\Program Files (x86)', r'C:\ProgramData')
               if os.name == 'nt')


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: str

    def matches(self, candidate):
        return self.category == candidate.category and (
            self.pattern == candidate.pattern if self.category == 'command'
            else Path(candidate.pattern).is_relative_to(Path(self.pattern)))


class Rules:
    def __init__(self, db, project):
        self.db, self.project = db, os.path.normcase(str(Path(project).resolve()))

    def rows(self):
        with self.db.transaction() as c:
            return c.execute('SELECT * FROM agent_rules WHERE project=? ORDER BY effect DESC,category,pattern',
                             (self.project,)).fetchall()

    def put(self, rule, effect):
        if effect not in ('allow','deny') or rule.category not in CATEGORIES:
            raise ValueError('无效的审批规则')
        if rule.category == 'command':
            if rule.pattern not in PREFIXES:
                raise ValueError('请选择列表中的命令前缀')
            if effect == 'allow' and rule.pattern in PROJECT_CODE | {'git add'}:
                raise ValueError('此命令需要逐项确认，可添加拒绝规则')
        elif not Path(rule.pattern).is_absolute():
            raise ValueError('文件夹规则需要绝对路径')
        with self.db.transaction() as c:
            c.execute('INSERT OR IGNORE INTO agent_rules(project,category,pattern,effect) VALUES (?,?,?,?)',
                      (self.project,rule.category,rule.pattern,effect))

    def remove(self, rule, effect):
        with self.db.transaction() as c:
            c.execute('DELETE FROM agent_rules WHERE project=? AND category=? AND pattern=? AND effect=?',
                      (self.project,rule.category,rule.pattern,effect))

    def hit(self, candidate, reusable=True, deny_scopes=()):
        scopes = tuple(deny_scopes) + ((candidate,) if candidate else ())
        if not scopes:
            return None
        def check(c):
            rows = c.execute('SELECT * FROM agent_rules WHERE project=? AND category=? ORDER BY effect DESC,length(pattern) DESC',
                             (self.project, scopes[0].category))
            for row in rows:
                rule = Rule(row['category'], row['pattern'])
                targets = scopes if row['effect'] == 'deny' else ((candidate,) if candidate and reusable else ())
                if any(rule.matches(target) for target in targets):
                    c.execute('UPDATE agent_rules SET hits=hits+1 WHERE project=? AND category=? AND pattern=? AND effect=?',
                              (self.project,rule.category,row['pattern'],row['effect']))
                    return row['effect'], row['pattern']
        return self.db.locked(check)


@dataclass
class Request:
    run: int
    kind: str
    title: str
    detail: str
    rule: Rule | None
    peer: tuple | None
    deadline: float
    code: str = field(default_factory=lambda: secrets.token_hex(4).upper())
    ready: threading.Event = field(default_factory=threading.Event)
    decision: str = ''
    reason: str = ''

    def __post_init__(self):
        # 批量与记住规则均以这一检查为准，即使上游误传了候选也不能放行硬底线。
        if self.rule and (self.kind != self.rule.category or self.rule.category not in CATEGORIES
                          or self.kind == 'command' and self.rule.pattern in PROJECT_CODE | {'git add'}):
            self.rule = None

    def describe(self):
        return f'[{self.code}] {self.title}'+(f'\n可复用规则：{CATEGORIES[self.rule.category]} {self.rule.pattern}' if self.rule else '')


class Approvals:
    def __init__(self, changed=lambda:None, clock=time.monotonic):
        self.lock = threading.RLock()
        self.pending = {}
        self.clock, self.changed = clock, changed

    def add(self, run, kind, title, detail='', rule=None, peer=None, timeout=120):
        with self.lock:
            req = Request(run, kind, title, detail, rule, peer, self.clock() + timeout)
            while req.code in self.pending:
                req.code = secrets.token_hex(4).upper()
            self.pending[req.code] = req
        self.changed()
        return req

    def items(self):
        with self.lock:
            return list(self.pending.values())

    def answer(self, code, decision, reason='', *, peer=None):
        with self.lock:
            req = self.pending.get(code)
            if not req or (peer is not None and req.peer != peer):
                return False
            expired = self.clock() >= req.deadline
            if expired:
                decision = 'timeout'
            if decision not in ('once','no','timeout','always','persist') or (
                    decision in ('always','persist') and not req.rule):
                return False
            del self.pending[code]
            req.decision, req.reason = decision, reason[:300]
            req.ready.set()
        self.changed()
        return not expired

    def batch(self, codes, run, peer=None):
        with self.lock:
            return sum(self.answer(code,'once',peer=peer) for code in codes if
                       (item:=self.pending.get(code)) and item.run==run and item.rule)
