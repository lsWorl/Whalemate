from PySide6.QtCore import QThread, Signal
from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled


class ChatWorker(QThread):
    """worker 自己拥有取消令牌，所有结果携带 generation_id。"""

    token_received = Signal(str,str)
    expression_event = Signal(str,object)
    result = Signal(str,str,str)

    def __init__(self,controller,generation_id,character,query='',proactive='',parent=None):
        super().__init__(parent)
        self.controller,self.generation_id,self.character = controller,generation_id,character
        self.query,self.proactive = query,proactive
        self.cancellation = CancellationToken()

    def run(self):
        try:
            from aicompanion.expression.domain import ExpressionContext,owner_relationship
            # 桌面上说话的就是用户本人（她爹）：和 QQ 绑定号的私聊一样，可以用亲昵 / 撒娇类表情
            context=ExpressionContext(self.generation_id,self.character.id,'desktop:'+self.character.id,
                       trigger='proactive' if self.proactive else 'user_reply',proactive_reason=self.proactive,early_before=True,
                       relationship=owner_relationship())
            outcome=['complete']
            def expression(event):
                if event.result:outcome[0]=event.result.status
                media=None
                attachment=event.attachment or (event.result.attachments[0] if event.result and event.result.attachments else None)
                if attachment:
                    media=self.controller.expression.catalog.resolve(attachment.asset_id,attachment.revision)
                self.expression_event.emit(self.generation_id,('expression',event,media))
            for token in self.controller.generate(self.generation_id,self.character,self.cancellation,self.query,self.proactive,
                                                  expression_context=context,on_expression_event=expression):
                self.token_received.emit(self.generation_id,token)
                self.expression_event.emit(self.generation_id,('text',token))
            self.result.emit(self.generation_id,outcome[0] if outcome[0]!='suppressed' else 'complete','回复过滤失败' if outcome[0]=='failed' else '')
        except ReplyCancelled:
            self.result.emit(self.generation_id,'cancelled','')
        except Exception as error:
            self.result.emit(self.generation_id,'failed',str(error))


class TaskWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self,function,parent=None):
        super().__init__(parent)
        self.function = function
        self.cancellation = CancellationToken()

    def run(self):
        try:
            value = self.function(self.cancellation)
            self.cancellation.check()
            self.succeeded.emit(value)
        except ReplyCancelled:
            pass
        except Exception as error:
            self.failed.emit(str(error))
