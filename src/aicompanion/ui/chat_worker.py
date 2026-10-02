from PySide6.QtCore import QThread, Signal
from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled


class ChatWorker(QThread):
    """worker 自己拥有取消令牌，所有结果携带 generation_id。"""

    token_received = Signal(str,str)
    result = Signal(str,str,str)

    def __init__(self,controller,generation_id,character,query='',proactive='',parent=None):
        super().__init__(parent)
        self.controller,self.generation_id,self.character = controller,generation_id,character
        self.query,self.proactive = query,proactive
        self.cancellation = CancellationToken()

    def run(self):
        try:
            for token in self.controller.generate(self.generation_id,self.character,self.cancellation,self.query,self.proactive):
                self.token_received.emit(self.generation_id,token)
            self.result.emit(self.generation_id,'complete','')
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
