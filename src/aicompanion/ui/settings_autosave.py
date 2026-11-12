"""只为可持久化的设置排队。草稿表单、导航、表格选择和后台状态刷新不触发写入。"""
from functools import partial

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget,
                               QPlainTextEdit, QSpinBox)


class SettingsAutosave(QObject):
    failed = Signal(str)

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.pending = {}
        self.errors = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.flush)
        d = dialog
        self.watch('identity', d.save_identity, d.name, d.model, d.avatar_mode)
        self.watch('voice', d.save_voice, d.provider, d.voice)
        cid = d.controller.character.id
        for group, key, widget in (
            ('voice', f'qwen_style:{cid}', d.voice_style), ('voice', f'qwen_mood:{cid}', d.voice_mood),
            ('voice', f'speech_language:{cid}', d.speech_language), ('voice', f'japanese_style:{cid}', d.japanese_style),
            ('voice', 'speech_style', d.speech_style), ('capture', 'input_device', d.input),
            ('capture', 'asr_provider', d.asr_provider), ('capture', 'voice_mode', d.voice_mode),
            ('capture', 'echo_backend', d.echo_backend), ('capture', 'ptt_key', d.ptt_key),
            ('background', 'panel_background', d.panel_background), ('background', 'panel_background_opacity', d.panel_opacity),
            ('memory', 'memory_analysis', d.analysis), ('proactive', 'proactive_enabled', d.proactive),
            ('proactive', 'quiet_start', d.quiet_start), ('proactive', 'quiet_end', d.quiet_end),
            ('proactive', 'proactive_daily_limit', d.proactive_limit), ('proactive', 'proactive_gap_minutes', d.proactive_gap),
        ):
            self.watch(group, partial(self.save_value, key, widget), widget)
        agent = d.agent_page
        self.watch('agent', agent.save, agent.enabled, agent.auto, agent.workspace, agent.blog, agent.keep_days, agent.max_mb)
        usage = d.usage_page
        self.watch('usage', usage.save, usage.total, *usage.limits.values(), usage.prices)
        def storage_applied():
            # 两个页面展示同一图库目录。同步回显时不再触发下一轮自动保存。
            bridge = d.bridge_page
            if bridge.meme_root.text().strip() == bridge.loaded_meme_root:
                path = d.database.get_setting('qq_meme_root', '')
                bridge.meme_root.blockSignals(True)
                bridge.meme_root.setText(path)
                bridge.meme_root.blockSignals(False)
                bridge.loaded_meme_root = path
            d.saved.emit('storage')
        d.storage_page.applied.connect(storage_applied)
        life = d.life_card
        self.watch('life', lambda: life.life.set_enabled(life.enabled.isChecked()), life.enabled)
        self.watch('life', partial(self.save_value, 'life_daily_activities', life.daily), life.daily)
        persona = d.persona_editor
        persona.edited.connect(lambda: self.schedule('persona', persona.autosave))
        bridge = d.bridge_page
        bridge.connection_saved.connect(lambda: d.saved.emit('platforms'))
        self.watch('platforms', bridge.save_preferences, bridge.owner, bridge.groups, bridge.chime, bridge.strangers,
                   bridge.vision_enabled, bridge.proactive_qq, bridge.memes_enabled, bridge.meme_root,
                   bridge.meme_cooldown, bridge.avatar_enabled, bridge.learning, bridge.chat_card_enabled,
                   bridge.community_auto, bridge.community_groups, bridge.community_source,
                   bridge.daily, bridge.autostart, bridge.index)
        if d.model_page:
            d.model_page.persisted.connect(lambda: d.saved.emit('models'))
            d.model_page.save_failed.connect(self.failed)

    def watch(self, group, save, *widgets):
        for widget in widgets:
            if isinstance(widget, QComboBox):
                signal = widget.currentTextChanged
            elif isinstance(widget, QCheckBox):
                signal = widget.toggled
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                signal = widget.valueChanged
            elif isinstance(widget, QListWidget):
                signal = widget.itemChanged
            else:
                signal = widget.textChanged
            signal.connect(lambda *args, g=group, fn=save: self.schedule(g, fn))

    def save_value(self, key, widget):
        if isinstance(widget, QComboBox):
            value = widget.currentData()
        elif isinstance(widget, QCheckBox):
            value = widget.isChecked()
        elif isinstance(widget, QPlainTextEdit):
            value = widget.toPlainText()
        elif isinstance(widget, QLineEdit):
            value = widget.text().strip()
        else:
            value = widget.value()
        if key == 'panel_background_opacity':
            value /= 100
        self.dialog.database.set_setting(key, value)

    def schedule(self, group, save):
        self.pending[save] = group
        self.timer.start()

    def flush(self):
        self.timer.stop()
        pending, self.pending = self.pending, {}
        saved = set()
        for save, group in pending.items():
            try:
                save()
            except Exception as error:
                self.errors[save] = str(error)
            else:
                self.errors.pop(save, None)
                saved.add(group)
        for group in sorted(saved):
            self.dialog.saved.emit(group)
        if self.errors:
            self.failed.emit(next(iter(self.errors.values())))
        return not self.errors
