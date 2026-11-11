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

    @property
    def total(self) -> int: return self.prompt+self.history

    @property
    def over(self) -> bool: return self.total > self.limit

    def as_dict(self) -> dict:
        return {'purpose': self.purpose, 'limit': self.limit, 'prompt': self.prompt, 'history': self.history,
                'turns': self.turns, 'dropped': self.dropped, 'sections': self.sections, 'total': self.total, 'at': self.at}


def limit_for(purpose: str, window: int | None = None, max_output: int | None = None) -> int:
    limit = LIMITS.get(purpose, LIMITS['default'])
    if window:
        usable = int((window*WINDOW_SHARE-(max_output or 0))*CHARS_PER_TOKEN)
        if usable > 0: limit = min(limit, usable)
    return limit


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
    report = Report(purpose, limit, len(prompt), sum(len(t.content) for t in kept), len(kept), dropped, dict(sections or {}), clock())
    return kept, report


def save(database, character_id: str, report: Report) -> None:
    try: database.set_setting(f'context_report:{character_id}', report.as_dict())
    except Exception: pass   # 记报告失败不影响聊天


def describe(data: dict | None) -> str:
    """记忆页上显示的一行说明。"""
    if not data: return '还没有记录（聊一句之后这里会显示上一轮请求带了多少内容）。'
    names = {'persona': '人格', 'memory': '记忆', 'recall': '想起的旧事', 'relationship': '关系', 'expression': '表情候选',
             'tools': '办事说明', 'other': '其他说明'}
    parts = '、'.join(f"{names.get(k, k)} {v} 字" for k, v in (data.get('sections') or {}).items() if v)
    text = (f"上一轮请求共 {data.get('total', 0)} 字（预算 {data.get('limit', 0)} 字）：系统提示 {data.get('prompt', 0)} 字"
            + (f"（{parts}）" if parts else '') + f"，聊天记录 {data.get('turns', 0)} 条 {data.get('history', 0)} 字")
    if data.get('dropped'): text += f"；为了不超预算，最早的 {data['dropped']} 条聊天这次没带上"
    if data.get('total', 0) > data.get('limit', 0): text += '。系统提示本身已超过预算，可以精简人格或世界书'
    return text+'。'
