"""表达数据不接受模型指定的路径、URL 或人物关系评分。"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import re
ASSET_ID = re.compile(r'[a-z][a-z0-9_-]{7,79}\Z')
PLACEMENTS = ('before_text', 'after_text')

@dataclass(frozen=True)
class RelationshipSnapshot:
    identity_state: str = 'unknown'
    person_ref: str | None = None
    relationship_kind: str = 'neutral'
    intimacy_allowed: bool = False
    link_state: str = 'unlinked'
    closeness: float = 0.0    # T15：好感和熟悉算出的 0～1，只影响候选排序（熟人多给调侃 / 接梗类），不授予任何权限

@dataclass(frozen=True)
class CommunityContext:
    community_key: str | None = None
    entries: tuple[str, ...] = ()

@dataclass(frozen=True)
class ExpressionCandidate:
    asset_id: str
    revision: str
    summary: str
    emotions: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    image_text: str = ''
    category: str = ''
    tone_strength: int = 1
    handle: str = ''          # 本轮给模型看的短编号 s1～s10；每轮重新分配，只在这一轮有效
    learned: str = ''         # 向群友学到的用法（"群友在「…」时发过" / "群友常用"），见 expression/usage.py

@dataclass(frozen=True)
class ExpressionContext:
    generation_id: str
    character_id: str
    scope: str
    channel: str = 'desktop'
    scene: str = 'pet'
    trigger: str = 'user_reply'
    proactive_reason: str = ''
    relationship: RelationshipSnapshot = field(default_factory=RelationshipSnapshot)
    community: CommunityContext = field(default_factory=CommunityContext)
    no_memes: bool = False
    more_memes: bool = False
    supports_images: bool = True
    early_before: bool = False

def owner_relationship() -> 'RelationshipSnapshot':
    """已绑定的用户本人（桌面使用者 / QQ 绑定号的私聊）。沿用现有的"爹"绑定，不是 T15 的关系评分；
    陌生人、群聊仍是默认的中性快照。"""
    return RelationshipSnapshot('verified', 'owner', 'owner_family', True, 'bound', 0.8)


@dataclass(frozen=True)
class ExpressionDecision:
    action: str = 'text_only'
    asset_id: str | None = None
    placement: str | None = None
    origin: str = 'no_marker'

@dataclass(frozen=True)
class ExpressionAttachment:
    asset_id: str
    revision: str
    placement: str
    delivery_id: str
    media_type: str
    content_sha256: str
    alt_text: str

@dataclass(frozen=True)
class ExpressionResult:
    turn_id: str
    generation_id: str
    character_id: str
    delivery_scope: str
    text: str
    attachments: tuple[ExpressionAttachment, ...] = ()
    decision: ExpressionDecision = field(default_factory=ExpressionDecision)
    status: str = 'complete'
    diagnostics: tuple[str, ...] = ()
    version: int = 1
    def wire(self): return asdict(self)

@dataclass(frozen=True)
class ExpressionEvent:
    kind: str
    generation_id: str
    attachment: ExpressionAttachment | None = None
    result: ExpressionResult | None = None
