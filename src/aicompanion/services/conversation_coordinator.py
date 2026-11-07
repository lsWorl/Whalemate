from time import monotonic
from uuid import uuid4


class ConversationCoordinator:
    """无 Qt 的轮次合并和过期事件判断，适配器负责计时和执行。"""

    def __init__(self,quiet_seconds=1.2,max_seconds=4,clock=monotonic):
        self.clock=clock; self.quiet=quiet_seconds; self.maximum=max_seconds
        self.pending=[]; self.first_at=None; self.last_at=None; self.active=None

    def submit(self,text):
        now=self.clock()
        if self.first_at is None: self.first_at=now
        self.last_at=now; self.pending.append(text)
        self.active=None

    def ready(self):
        if not self.pending: return False
        now=self.clock()
        return now-self.last_at>=self.quiet or now-self.first_at>=self.maximum

    def begin(self):
        query='\n'.join(self.pending); self.pending=[]; self.first_at=self.last_at=None
        self.active=uuid4().hex
        return self.active,query

    def accepts(self,generation_id): return generation_id==self.active

    def cancel(self,clear_pending=False):
        self.active=None
        if clear_pending:
            self.pending=[]; self.first_at=self.last_at=None
