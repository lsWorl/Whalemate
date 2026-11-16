"""人格试聊只持有本窗口的对话，关闭、清空或换人格都会丢弃。"""
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from aicompanion.domain.persona import trial_prompt
from aicompanion.llm.base import ChatTurn
from aicompanion.services import budget
from aicompanion.services.persona_service import PersonaService
from aicompanion.ui import theme
from aicompanion.ui.chat_view import ChatView
from aicompanion.ui.chat_worker import TaskWorker
from aicompanion.usage import meter


class PersonaTrial(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.character = window.controller.character
        self.history = []
        self.worker = None
        self.setWindowTitle('人格试聊')
        self.setStyleSheet(theme.DIALOG)
        self.resize(480, 540)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel('试聊人格'))
        self.picker = QComboBox()
        self.picker.setAccessibleName('试聊人格')
        service = PersonaService(window.database)
        active = service.active(self.character.id) or service.ensure(self.character)
        for persona in service.personas(self.character.id):
            self.picker.addItem(persona.title, persona)
            if persona.id == active.id:
                self.picker.setCurrentIndex(self.picker.count() - 1)
        row.addWidget(self.picker, 1)
        clear = QPushButton('清空试聊')
        clear.clicked.connect(self.reset)
        row.addWidget(clear)
        layout.addLayout(row)
        note = QLabel('仅使用所选人格和本次试聊内容。换人格或关闭窗口会清空试聊。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.chat = ChatView()
        layout.addWidget(self.chat, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        compose = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText('输入试聊消息')
        self.input.returnPressed.connect(self.send)
        compose.addWidget(self.input, 1)
        self.send_button = QPushButton('发送')
        self.send_button.clicked.connect(self.send)
        compose.addWidget(self.send_button)
        layout.addLayout(compose)
        self.picker.currentIndexChanged.connect(self.reset)
        # 回车只发送输入框里的消息，避免同时触发对话框的默认按钮。
        for button in (clear, self.send_button):
            button.setAutoDefault(False)
        self.input.setFocus()

    def reset(self):
        if self.worker:
            self.worker.cancellation.cancel()
            self.worker = None
        self.history.clear()
        self.question = ''
        self.chat.clear()
        self.input.clear()
        self.status.clear()
        self.send_button.setEnabled(True)

    def done(self, result):
        self.reset()
        super().done(result)

    def send(self):
        question = self.input.text().strip()
        if self.worker or not question:
            return
        state, note = self.host.quota.check('companion')
        if state == 'stop':
            self.status.setText(note)
            return
        persona = self.picker.currentData()
        prompt = trial_prompt(persona, self.character.name, question)
        try:
            client, model = self.host.controller.router.client('chat', self.character.id, 'companion')
        except Exception as error:
            self.status.setText(str(error))
            return
        turns, report = budget.fit([*self.history, ChatTurn('user', question)], prompt, 'companion',
                                   getattr(model, 'context_window', None), getattr(model, 'max_output', None))
        if report.blocked:
            self.status.setText(budget.refusal(report))
            return
        cid = self.character.id

        def reply(token):
            with meter.using('companion', cid):
                answer = ''.join(client.generate_reply_stream(turns, prompt, token)).strip()
            if not answer:
                raise ValueError('模型没有返回文字，请重试。')
            return answer

        self.chat.append('你', question)
        self.input.clear()
        self.question = question
        self.send_button.setEnabled(False)
        self.status.setText('正在回复…')
        # 主窗口负责退出时取消和回收线程；对话框关闭后，Qt 自动断开其接收槽。
        worker = self.worker = TaskWorker(reply, self.host)
        worker.succeeded.connect(self._received)
        worker.failed.connect(self._failed)
        self.host.tasks.add(worker)
        host = self.host
        worker.finished.connect(lambda: host._task_finished(worker))
        worker.start()

    @Slot(object)
    def _received(self, answer):
        if self.sender() != self.worker:
            return
        self.worker = None
        self.history.extend((ChatTurn('user', self.question), ChatTurn('assistant', answer)))
        self.chat.append(self.character.name, answer)
        self.status.clear()
        self.send_button.setEnabled(True)

    @Slot(str)
    def _failed(self, error):
        if self.sender() != self.worker:
            return
        self.worker = None
        self.status.setText('试聊失败：' + error)
        if not self.input.text():
            self.input.setText(self.question)
        self.send_button.setEnabled(True)
