"""整份请求的预算（计划书 v4 功能六，阶段二）：人格、记忆、召回、关系、表情候选、工具说明、历史全部算进去，每种用途有自己的上限。

为什么按字数而不是词元：各家分词器不同，字数在本地就能算、和费用大致成正比；中文一个字大约 0.6～1 个词元，按 1 估算偏保守。
即使模型支持 100 万窗口也不填满——费用和速度才是真正的约束。

放不下时的取舍顺序：
1. 召回的旧聊天、旧事已经在拼提示词时按自己的份额截好（RecallService / Recalled.render）；
2. 从最早的历史开始丢，但最后一组用户消息（当前要回应的话）永远保留；
3. 系统提示本身（人格核心、当前要求、安全说明）不删——如果它单独就超了，照常发出并在报告里标“超出”，交给人看，
   不悄悄删掉人格或要求。
每次请求的明细存在 app_settings（context_report:<角色>），记忆页里能看到上一轮带了多少。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# 初值：聊天沿用原来的“历史 12000 字”再加上系统提示的余量；按召回效果和费用曲线再调（计划书 v4）
LIMITS = {'companion': 20000, 'qq': 14000, 'proactive': 10000, 'default': 16000}
WINDOW_SHARE = 0.5          # 已知模型窗口时，最多用到窗口的一半（其余留给输出和思考）
CHARS_PER_TOKEN = 1.0


class BudgetExceeded(RuntimeError):
    """人格、当前要求这些不能删的内容已经超过模型的上下文上限：这次不发送（发了也会被服务拒绝或截断，还白花钱）。"""


@dataclass
class Report:
    purpose: str
    limit: int
    prompt: int
    history: int
    turns: int
    dropped: int = 0
    sections: dict = field(default_factory=dict)
    at: float = 0.0
    hard_limit: int | None = None     # 已知模型窗口时的硬上限（窗口减去输出预留）；超过就不发送

    @property
    def total(self) -> int: return self.prompt+self.history

    @property
    def over(self) -> bool: return self.total > self.limit

    @property
    def blocked(self) -> bool:
        """确定会超过已知模型上限（Codex PR #38 第 4 项）：软预算超了只裁历史、照常发；硬上限超了不发。"""
        return self.hard_limit is not None and self.total > self.hard_limit

    def as_dict(self) -> dict:
        return {'purpose': self.purpose, 'limit': self.limit, 'prompt': self.prompt, 'history': self.history,
                'turns': self.turns, 'dropped': self.dropped, 'sections': self.sections, 'total': self.total, 'at': self.at,
                'hard_limit': self.hard_limit, 'blocked': self.blocked}


def limit_for(purpose: str, window: int | None = None, max_output: int | None = None) -> int:
    """软预算：按它裁历史。已知窗口时不超过“窗口一半减输出预留”；输出预留吃光了窗口就是 0（只留不能删的内容），
    不再退回默认的 20000 字（Codex PR #38 第 4 项）。"""
    limit = LIMITS.get(purpose, LIMITS['default'])
    if window:
        limit = max(0, min(limit, int((window*WINDOW_SHARE-(max_output or 0))*CHARS_PER_TOKEN)))
    return limit


def hard_limit_for(window: int | None = None, max_output: int | None = None) -> int | None:
    """硬上限：模型窗口减去输出预留（按字数估）。窗口未知时没有硬上限。"""
    if not window: return None
    return max(0, int((window-(max_output or 0))*CHARS_PER_TOKEN))


def fit(turns: list, prompt: str, purpose: str, window: int | None = None, max_output: int | None = None,
        sections: dict | None = None, clock=time.time):
    """按预算裁掉最早的历史。turns 的最后一条（当前要回应的话）保留；返回 (新的 turns, Report)。"""
    limit = limit_for(purpose, window, max_output)
    kept = list(turns); dropped = 0
    size = lambda: len(prompt)+sum(len(t.content) for t in kept)
    while len(kept) > 1 and size() > limit:
        kept.pop(0); dropped += 1
    # 开头不能是她自己的话（有些服务要求以用户消息开头）；丢掉开头她的半句也算裁剪
    while len(kept) > 1 and kept[0].role == 'assistant':
        kept.pop(0); dropped += 1
    report = Report(purpose, limit, len(prompt), sum(len(t.content) for t in kept), len(kept), dropped, dict(sections or {}), clock(),
                    hard_limit_for(window, max_output))
    return kept, report


def refusal(report: Report, label: str = '') -> str:
    """超过硬上限时给用户看的说明：不能删的部分多大、模型能收多少、怎么办。"""
    return (f"这次没有发送：人格、当前这句话等不能删掉的内容约 {report.total} 字，超过了{label or '当前模型'}能收的约 {report.hard_limit} 字"
            "（已经去掉了全部可以省的旧聊天）。可以精简人格或世界书、把这句话说短一点，或者在“模型与 Key”页换一个窗口更大的模型。")


def save(database, character_id: str, report: Report) -> None:
    try: database.set_setting(f'context_report:{character_id}', report.as_dict())
    except Exception: pass   # 记报告失败不影响聊天


def describe(data: dict | None) -> str:
    """记忆页上显示的一行说明。"""
    if not data: return '还没有记录（聊一句之后这里会显示上一轮请求带了多少内容）。'
    names = {'persona': '人格', 'memory': '记忆', 'recall': '想起的旧事', 'relationship': '关系', 'expression': '表情候选',
             'stages': '阶段摘要', 'attachments': '办事附件', 'tools': '办事说明', 'other': '其他说明'}
    parts = '、'.join(f"{names.get(k, k)} {v} 字" for k, v in (data.get('sections') or {}).items() if v)
    text = (f"上一轮请求共 {data.get('total', 0)} 字（预算 {data.get('limit', 0)} 字）：系统提示 {data.get('prompt', 0)} 字"
            + (f"（{parts}）" if parts else '') + f"，聊天记录 {data.get('turns', 0)} 条 {data.get('history', 0)} 字")
    if data.get('dropped'): text += f"；为了不超预算，最早的 {data['dropped']} 条聊天这次没带上"
    if data.get('blocked'): text += '。超过了模型的上下文上限，这一轮没有发送；请精简人格或世界书，或换窗口更大的模型'
    elif data.get('total', 0) > data.get('limit', 0): text += '。系统提示本身已超过预算，可以精简人格或世界书'
    return text+'。'
