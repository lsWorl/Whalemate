from dataclasses import replace
from uuid import uuid4
from datetime import timezone
from PySide6.QtCore import Qt,QDateTime,QTimeZone
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTabWidget,QWidget,QFormLayout,QLineEdit,
    QPlainTextEdit,QPushButton,QCheckBox,QComboBox,QSpinBox,QTableWidget,QTableWidgetItem,QLabel,QFileDialog,QMessageBox,QDateTimeEdit,QScrollArea)
from aicompanion.speech.qwen_client import DEFAULT_STYLE
from aicompanion.avatar.model_package import validate_model_package, ModelPackageError


class SettingsWindow(QDialog):
    def __init__(self,controller,settings,parent=None,speech_output=None):
        super().__init__(parent)
        self.controller=controller; self.database=controller.database; self.settings=settings
        self.speech_output=speech_output
        self.setWindowTitle('设置'); self.resize(780,640)
        from aicompanion.ui import theme; self.setStyleSheet(theme.DIALOG)
        layout=QVBoxLayout(self); self.tabs=QTabWidget(); layout.addWidget(self.tabs)
        role=QWidget(); form=QFormLayout(role)
        self.name=QLineEdit(controller.character.name)
        self.description=QPlainTextEdit(controller.character.description)
        self.style=QPlainTextEdit(controller.character.speaking_style)
        self.model=QLineEdit(controller.character.model_path)
        self.avatar_mode=QComboBox()
        self.avatar_mode.addItem('角色包默认形象','sprites')
        self.avatar_mode.addItem('导入的 Live2D 模型','live2d')
        self.avatar_mode.addItem('艾米莉娅同人立绘预览（未绑定）','illustration')
        self.avatar_mode.addItem('艾米莉娅 2D 网格动画（非 Cubism）','animated-illustration')
        self.avatar_mode.setCurrentIndex(max(0,self.avatar_mode.findData(self.database.get_setting(f'avatar_mode:{controller.character.id}','sprites'))))
        choose=QPushButton('选择 Live2D 模型'); choose.clicked.connect(self.choose_model)
        form.addRow('角色名字',self.name); form.addRow('角色设定',self.description); form.addRow('说话习惯',self.style)
        form.addRow('角色显示',self.avatar_mode); form.addRow('model3.json',self.model); form.addRow('',choose)
        new=QPushButton('创建独立角色'); new.clicked.connect(self.create_character); form.addRow('',new)
        # 聊天面板外观：半透明背景立绘（本机图片，不随项目分发）
        self.panel_background=QLineEdit(self.database.get_setting('panel_background',''))
        self.panel_background.setPlaceholderText('留空 = data/private-art/panel-background.png')
        pick_bg=QPushButton('选择图片'); pick_bg.clicked.connect(self._pick_background)
        bg_row=QHBoxLayout(); bg_row.addWidget(self.panel_background,1); bg_row.addWidget(pick_bg)
        form.addRow('面板背景图',bg_row)
        self.panel_opacity=QSpinBox(); self.panel_opacity.setRange(0,100); self.panel_opacity.setSuffix(' %')
        self.panel_opacity.setValue(int(round(float(self.database.get_setting('panel_background_opacity',0.16) or 0)*100)))
        form.addRow('背景不透明度',self.panel_opacity)
        role_scroll=QScrollArea(); role_scroll.setWidgetResizable(True); role_scroll.setWidget(role)
        self.tabs.addTab(role_scroll,'角色')
        voice=QWidget(); voice_form=QFormLayout(voice)
        self.provider=QComboBox()
        for label,value in [('本地朗读','system'),('智谱语音','cloud'),('千问动漫声线','qwen')]: self.provider.addItem(label,value)
        self.provider.setCurrentIndex(max(0,self.provider.findData(self.database.get_setting('tts_provider',settings.tts_provider))))
        self.voice=QComboBox(); self.voice.setEditable(True)
        self._voice_provider_changed()
        self.provider.currentIndexChanged.connect(self._voice_provider_changed)
        self.voice_style=QPlainTextEdit(self.database.get_setting(f'qwen_style:{controller.character.id}',DEFAULT_STYLE))
        self.voice_style.setMaximumHeight(74)
        self.voice_mood=QComboBox()
        for label,value in [('跟随角色情绪','auto'),('温柔平静','calm'),('轻快开心','happy'),('含蓄害羞','shy'),('低缓失落','sad'),('轻微生气','angry')]: self.voice_mood.addItem(label,value)
        self.voice_mood.setCurrentIndex(max(0,self.voice_mood.findData(self.database.get_setting(f'qwen_mood:{controller.character.id}','auto'))))
        self.speech_language=QComboBox()
        self.speech_language.addItem('中文配音 / 中文字幕','zh')
        self.speech_language.addItem('日语配音 / 中文字幕','ja')
        self.speech_language.setCurrentIndex(max(0,self.speech_language.findData(self.database.get_setting(f'speech_language:{controller.character.id}',settings.speech_language))))
        self.japanese_style=QPlainTextEdit(self.database.get_setting(f'japanese_style:{controller.character.id}',''))
        self.japanese_style.setMaximumHeight(74)
        preview=QPushButton('试听声线'); preview.clicked.connect(self.preview_voice); preview.setEnabled(speech_output is not None)
        self.preview_status=QLabel(''); self.preview_status.setWordWrap(True)
        if speech_output:
            speech_output.error.connect(self.preview_status.setText)
            speech_output.speaking.connect(self._preview_started)
            self.finished.connect(self._finish_preview)
        self.input=QComboBox(); self.input.addItem('系统默认','')
        for device in QMediaDevices.audioInputs(): self.input.addItem(device.description(),bytes(device.id()).hex())
        index=self.input.findData(self.database.get_setting('input_device','')); self.input.setCurrentIndex(max(0,index))
        self.asr_provider=QComboBox()
        self.asr_provider.addItem('千问识别','qwen'); self.asr_provider.addItem('智谱识别','zhipu')
        self.asr_provider.addItem('本地识别（SenseVoice，离线、不计费）','local')
        # P4：收音方式、收音键、外放回声处理
        from aicompanion.voice.hotkey import KEYS, DEFAULT_KEY
        self.voice_mode=QComboBox()
        for text,value in [('持续通话：直接开口，可随时打断她','continuous'),('按住说话：按住收音键说，松开发送','ptt'),('切换式：按一下开始，再按一下结束','toggle')]: self.voice_mode.addItem(text,value)
        self.voice_mode.setCurrentIndex(max(0,self.voice_mode.findData(self.database.get_setting('voice_mode','continuous'))))
        self.ptt_key=QComboBox()
        for value,(text,_code) in KEYS.items(): self.ptt_key.addItem(text,value)
        self.ptt_key.setCurrentIndex(max(0,self.ptt_key.findData(self.database.get_setting('ptt_key',DEFAULT_KEY))))
        self.echo_backend=QComboBox()
        self.echo_backend.addItem('回声门控（播放时提高门槛，自动校准）','qt')
        self.echo_backend.addItem('浏览器回声消除（Chromium AEC3，播放也走网页）','web')
        self.echo_backend.setCurrentIndex(max(0,self.echo_backend.findData(self.database.get_setting('echo_backend','qt'))))
        self.asr_provider.setCurrentIndex(max(0,self.asr_provider.findData(self.database.get_setting('asr_provider',settings.asr_provider))))
        self.proactive=QCheckBox('允许早晚问候与主动聊天'); self.proactive.setChecked(self.database.get_setting('proactive_enabled',True))
        self.analysis=QCheckBox('自动分析长期记忆（会产生模型用量）'); self.analysis.setChecked(self.database.get_setting('memory_analysis',True))
        self.quiet_start=QSpinBox(); self.quiet_start.setRange(0,23); self.quiet_start.setValue(self.database.get_setting('quiet_start',23))
        self.quiet_end=QSpinBox(); self.quiet_end.setRange(0,23); self.quiet_end.setValue(self.database.get_setting('quiet_end',8))
        self.speech_style=QComboBox()
        for text,value in [('只说语气词（嘿嘿、诶？），文字看气泡','interjection'),('语气词 + 第一句短话','brief'),('完整朗读每一句','full')]: self.speech_style.addItem(text,value)
        self.speech_style.setCurrentIndex(max(0,self.speech_style.findData(self.database.get_setting('speech_style','interjection'))))
        voice_form.addRow('说话方式',self.speech_style)
        voice_form.addRow('朗读方式',self.provider); voice_form.addRow('声线',self.voice)
        voice_form.addRow('配音语言',self.speech_language)
        voice_form.addRow('日语口吻',self.japanese_style)
        voice_form.addRow('语气',self.voice_mood); voice_form.addRow('表达风格',self.voice_style)
        voice_form.addRow('',preview); voice_form.addRow('',self.preview_status); voice_form.addRow('麦克风',self.input)
        voice_form.addRow('语音识别服务',self.asr_provider)
        voice_form.addRow('收音方式',self.voice_mode); voice_form.addRow('收音键',self.ptt_key)
        voice_form.addRow('外放回声',self.echo_backend)
        self.voice_hint=QLabel(''); self.voice_hint.setWordWrap(True); voice_form.addRow('',self.voice_hint)
        voice_form.addRow('',self.proactive); voice_form.addRow('',self.analysis)
        voice_form.addRow('免打扰开始（小时）',self.quiet_start); voice_form.addRow('免打扰结束（小时）',self.quiet_end)
        voice_form.addRow('模型',QLabel(f'{settings.llm_provider} / {settings.llm_model}'))
        voice_form.addRow('千问合成',QLabel(settings.qwen_tts_model))
        self.asr_model=QLabel(''); self.asr_model.setWordWrap(True)
        self.asr_provider.currentIndexChanged.connect(self._recognition_provider_changed)
        self._recognition_provider_changed()
        voice_form.addRow('识别模型',self.asr_model)
        voice_scroll=QScrollArea(); voice_scroll.setWidgetResizable(True); voice_scroll.setWidget(voice)
        self.tabs.addTab(voice_scroll,'语音与互动')
        memories=QWidget(); ml=QVBoxLayout(memories)
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(['类型','记忆','清晰度','范围','更新于'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(1,420)
        ml.addWidget(self.table)
        buttons=QHBoxLayout(); refresh=QPushButton('刷新'); refresh.clicked.connect(self.refresh)
        delete=QPushButton('删除选中记忆'); delete.clicked.connect(self.delete_memory)
        private=QPushButton('设为只在私下用'); private.clicked.connect(lambda:self.set_memory_scope('private'))
        shared=QPushButton('设为共享'); shared.clicked.connect(lambda:self.set_memory_scope('pet'))
        buttons.addWidget(refresh); buttons.addWidget(delete); buttons.addWidget(private); buttons.addWidget(shared); ml.addLayout(buttons)
        self.archive_label=QLabel(''); ml.addWidget(self.archive_label)
        ml.addWidget(QLabel('记忆会随时间变模糊，低于 30% 就从记忆卡移出（全量存档仍保留，再提到会重新记住）。\n'
                            '手动删除 = 真的忘掉：存档里的同一事实也会删除，以后不会再被记住；聊天正文不受影响。\n'
                            '记忆在桌面和 QQ 之间共享（群里听来的事她在桌面上也记得）；标成"只在私下用"的，她不会在群聊和陌生人私聊里用。'))
        self.tabs.addTab(memories,'记忆')
        purposes=QWidget(); pl=QVBoxLayout(purposes)
        self.purpose_table=QTableWidget(0,4); self.purpose_table.setHorizontalHeaderLabels(['状态','类型','心里惦记的事','计划时间'])
        self.purpose_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.purpose_table.horizontalHeader().setStretchLastSection(True); self.purpose_table.setColumnWidth(2,380)
        pl.addWidget(self.purpose_table)
        pl.addWidget(QLabel('她会从清楚的记忆里想出想聊的事，在你在电脑前、不忙、非深夜时主动开口；每天最多 3 次，专注模式下暂停。'))
        self.tabs.addTab(purposes,'心事')
        reminders=QWidget(); rl=QVBoxLayout(reminders)
        self.reminder_table=QTableWidget(0,3); self.reminder_table.setHorizontalHeaderLabels(['内容','时间（UTC）','状态'])
        self.reminder_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.reminder_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.reminder_table.itemSelectionChanged.connect(self.select_reminder); rl.addWidget(self.reminder_table)
        self.reminder_title=QLineEdit(); self.reminder_date=QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600)); self.reminder_date.setCalendarPopup(True)
        self.reminder_date.setDisplayFormat('yyyy-MM-dd HH:mm'); rf=QFormLayout()
        rf.addRow('提醒内容',self.reminder_title); rf.addRow('本地时间',self.reminder_date); rl.addLayout(rf)
        rb=QHBoxLayout(); add=QPushButton('新增'); add.clicked.connect(lambda:self.save_reminder(False)); rb.addWidget(add)
        update=QPushButton('更新选中'); update.clicked.connect(lambda:self.save_reminder(True)); rb.addWidget(update)
        remove=QPushButton('删除选中'); remove.clicked.connect(self.delete_reminder); rb.addWidget(remove); rl.addLayout(rb)
        self.tabs.addTab(reminders,'提醒'); self.refresh_reminders()
        from aicompanion.ui.agent_pages import AgentPage, AuditPage, UsagePage
        self.agent_page=AgentPage(self.database); self.tabs.addTab(self.agent_page,'干活')
        self.audit_page=AuditPage(self.database); self.tabs.addTab(self.audit_page,'操作日志')
        from aicompanion.ui.bridge_page import BridgePage
        self.bridge_page=BridgePage(self.database); self.tabs.addTab(self.bridge_page,'聊天平台')
        self.usage_page=UsagePage(self.database)
        rows=self.database.usage_summary()
        other=QLabel('本月其他用量：'+'；'.join(f"{r['kind']} {r['amount']}" for r in rows)+'\nAPI 凭据在 .env 配置，不写入 SQLite。'); other.setWordWrap(True)
        self.usage_page.layout().addWidget(other)
        usage_scroll=QScrollArea(); usage_scroll.setWidgetResizable(True); usage_scroll.setWidget(self.usage_page)
        self.tabs.addTab(usage_scroll,'用量')
        save=QPushButton('保存'); save.clicked.connect(self.save); layout.addWidget(save)
        self.refresh()

    def _pick_background(self):
        path,_=QFileDialog.getOpenFileName(self,'选择背景图','', '图片 (*.png *.jpg *.jpeg *.webp)')
        if path: self.panel_background.setText(path)

    def _voice_provider_changed(self,*args):
        self.voice.clear()
        if self.provider.currentData()=='qwen':
            current=self.database.get_setting(f'qwen_voice:{self.controller.character.id}',self.settings.qwen_tts_voice)
            self.voice.addItem('自定义声线（AI 合成，非官方）',self.settings.qwen_tts_voice)
            if self.settings.qwen_original_tts_voice and self.settings.qwen_original_tts_voice!=self.settings.qwen_tts_voice:
                self.voice.addItem('原创动漫女声（备选）',self.settings.qwen_original_tts_voice)
            self.voice.addItem('灵希：可爱甜美','longanlingxi')
            self.voice.addItem('风悦：自然亲切','longanfengyue')
        else:
            current=self.controller.character.voice or self.database.get_setting('tts_voice','tongtong')
            self.voice.addItem('彤彤','tongtong')
        index=self.voice.findData(current)
        if index>=0: self.voice.setCurrentIndex(index)
        else: self.voice.setEditText(current)
        self.voice.setEnabled(self.provider.currentData()!='system')

    def _recognition_provider_changed(self,*args):
        provider=self.asr_provider.currentData()
        if provider=='local':
            from aicompanion.voice import local_asr
            problem=local_asr.status()
            self.asr_model.setText(problem or f'SenseVoice（{local_asr.find_model()[0].parent.name}），首次识别时加载，约占 250 MB，闲置 10 分钟释放')
        else:
            self.asr_model.setText(self.settings.qwen_asr_model if provider=='qwen' else self.settings.asr_model)
        if hasattr(self,'voice_hint'):
            coupling=self.database.get_setting('echo_coupling')
            self.voice_hint.setText('外放时她说话也能被你打断；声音很大或离麦克风很近时，建议改用按住说话。'
                                    +(f'（已校准回声系数 {coupling}）' if coupling is not None else '（第一次外放通话时自动校准）'))

    def selected_voice(self):
        index=self.voice.currentIndex()
        if index>=0 and self.voice.currentText()==self.voice.itemText(index):
            return self.voice.itemData(index)
        return self.voice.currentText().strip()

    def preview_voice(self):
        if not self.speech_output: return
        speech=self.speech_output; speech.stop(); speech.enabled=True
        speech.provider=self.provider.currentData(); speech.voice=self.selected_voice()
        speech.language=self.speech_language.currentData()
        speech.role_style=self.japanese_style.toPlainText()
        mood=self.voice_mood.currentData()
        speech.mood=self.database.load_emotion(self.controller.character.id).mood if mood=='auto' else mood
        speech.style=self.voice_style.toPlainText()
        # 系统朗读可能同步触发开始信号，先显示准备状态，避免覆盖正在试听的提示。
        self.preview_status.setText('正在准备试听，云端合成会产生少量用量。')
        speech.enqueue('你回来啦。今天辛苦了，你可以慢慢说，我会认真听着的。',uuid4().hex)

    def _preview_started(self,active):
        if active: self.preview_status.setText('正在试听')

    def _finish_preview(self,*args):
        self.speech_output.stop()
        self.speech_output.error.disconnect(self.preview_status.setText)
        self.speech_output.speaking.disconnect(self._preview_started)

    def choose_model(self):
        path,_=QFileDialog.getOpenFileName(self,'选择模型','','Live2D model (*.model3.json)')
        if path:
            try: validate_model_package(path)
            except (ModelPackageError,OSError) as error:
                QMessageBox.warning(self,'模型包无效',str(error)); return
            self.model.setText(path)
            self.avatar_mode.setCurrentIndex(self.avatar_mode.findData('live2d'))

    def create_character(self):
        from aicompanion.domain.character import CharacterProfile
        name=self.name.text().strip()
        if not name or not self._validate_model_input(): return
        character=CharacterProfile(name,self.description.toPlainText(),self.style.toPlainText(),uuid4().hex,self.model.text().strip(),
            '' if self.provider.currentData()=='qwen' else self.selected_voice())
        self.database.add_character(character)
        if self.provider.currentData()=='qwen':
            self.database.set_setting(f'qwen_voice:{character.id}',self.selected_voice())
        # 新角色沿用编辑中的声音风格，但设置键按角色隔离，之后可独立调整。
        self.database.set_setting(f'qwen_style:{character.id}',self.voice_style.toPlainText())
        self.database.set_setting(f'qwen_mood:{character.id}',self.voice_mood.currentData())
        self.database.set_setting(f'speech_language:{character.id}',self.speech_language.currentData())
        self.database.set_setting(f'avatar_mode:{character.id}',self.avatar_mode.currentData())
        self.database.set_setting(f'japanese_style:{character.id}',self.japanese_style.toPlainText())
        QMessageBox.information(self,'已创建','角色已创建，关闭设置后可在主界面切换。')

    def refresh(self):
        rows=self.database.recent_memories(1000,self.controller.character.id); self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            clarity=float(row['clarity']); state='清楚' if clarity>=0.6 else '模糊'
            scope={'private':'只在私下用','pet':'共享','qq':'共享（QQ 上听来的）','bili':'共享（B站上听来的）'}.get(row['scope'],row['scope'])
            values=(row['category'],row['content'],f'{state} {clarity:.0%}',scope,row['updated_at'])
            for j,value in enumerate(values):
                item=QTableWidgetItem(str(value)); item.setData(Qt.ItemDataRole.UserRole,row['id']); self.table.setItem(i,j,item)
        archive=self.database.memory_archive(self.controller.character.id,100000)
        self.archive_label.setText(f'记忆卡 {len(rows)} 条 · 全量存档 {len(archive)} 条（其中已淡忘 {sum(r["reason"] in ("forgotten","trimmed") for r in archive)} 条）')
        kinds={'understand':'想弄明白','do':'想做','share':'想分享','ask':'想向你要'}
        status={'pending':'等待时机','delivering':'正在说','delivered':'已说过','expired':'已过期','dropped':'放弃'}
        purposes=self.database.purposes(self.controller.character.id,None,100); self.purpose_table.setRowCount(len(purposes))
        for i,row in enumerate(purposes):
            for j,value in enumerate((status.get(row['status'],row['status']),kinds.get(row['kind'],row['kind']),row['content'],row['due_at'].replace('T',' ')[:16])):
                self.purpose_table.setItem(i,j,QTableWidgetItem(value))

    def set_memory_scope(self,scope):
        """记忆默认在桌面和 QQ 共享；"只在私下用"的不会在群聊和陌生人私聊里用。"""
        rows={index.row() for index in self.table.selectedIndexes()}
        # 标成私下时，内容高度重合的副本（例如之前从群里整理出来的）一起标成私下
        self.controller.memory_service.set_scope([self.table.item(r,0).data(Qt.ItemDataRole.UserRole) for r in rows],self.controller.character.id,scope)
        self.refresh()

    def delete_memory(self):
        selected={index.row() for index in self.table.selectedIndexes()}
        for row in selected: self.database.delete_memory(self.table.item(row,0).data(Qt.ItemDataRole.UserRole),self.controller.character.id)
        if selected:
            history=self.database.recent_messages(1,self.controller.character.id)
            self.database.set_setting(f'context_after:{self.controller.character.id}',history[-1]['id'] if history else 0)
        self.refresh()

    def save(self):
        if not self.name.text().strip() or not self._validate_model_input(): return
        character=replace(self.controller.character,name=self.name.text().strip(),description=self.description.toPlainText(),
                          speaking_style=self.style.toPlainText(),model_path=self.model.text().strip(),
                          voice=self.controller.character.voice if self.provider.currentData()=='qwen' else self.selected_voice())
        self.database.update_character(character); self.controller.character=character
        if self.provider.currentData()=='qwen':
            self.database.set_setting(f'qwen_voice:{character.id}',self.selected_voice())
        self.database.set_setting(f'qwen_style:{character.id}',self.voice_style.toPlainText())
        self.database.set_setting(f'qwen_mood:{character.id}',self.voice_mood.currentData())
        self.database.set_setting(f'speech_language:{character.id}',self.speech_language.currentData())
        self.database.set_setting(f'avatar_mode:{character.id}',self.avatar_mode.currentData())
        self.database.set_setting(f'japanese_style:{character.id}',self.japanese_style.toPlainText())
        for key,value in [('tts_provider',self.provider.currentData()),('input_device',self.input.currentData()),
                          ('asr_provider',self.asr_provider.currentData()),('voice_mode',self.voice_mode.currentData()),
                          ('speech_style',self.speech_style.currentData()),
                          ('panel_background',self.panel_background.text().strip()),('panel_background_opacity',self.panel_opacity.value()/100),
                          ('ptt_key',self.ptt_key.currentData()),('echo_backend',self.echo_backend.currentData()),
                          ('proactive_enabled',self.proactive.isChecked()),('memory_analysis',self.analysis.isChecked()),
                          ('quiet_start',self.quiet_start.value()),('quiet_end',self.quiet_end.value())]: self.database.set_setting(key,value)
        self.agent_page.save(); self.usage_page.save(); self.bridge_page.save()
        self.accept()

    def _validate_model_input(self):
        # 手动输入路径也要检查，不能只在文件选择器里拦截损坏的模型包。
        if self.avatar_mode.currentData()=='live2d' and self.model.text().strip():
            try: validate_model_package(self.model.text().strip())
            except (ModelPackageError,OSError) as error:
                QMessageBox.warning(self,'模型包无效',str(error)); return False
        return True

    def refresh_reminders(self):
        rows=self.database.reminders(self.controller.character.id); self.reminder_table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,key in enumerate(('title','due_at','status')):
                item=QTableWidgetItem(str(row[key])); item.setData(Qt.ItemDataRole.UserRole,row['id']); self.reminder_table.setItem(i,j,item)

    def select_reminder(self):
        row=self.reminder_table.currentRow()
        if row>=0 and self.reminder_table.item(row,0):
            self.reminder_title.setText(self.reminder_table.item(row,0).text())
            # 数据库时间为 UTC；编辑器显示本地时间，避免只改标题却覆盖到期时间。
            date=QDateTime.fromString(self.reminder_table.item(row,1).text(),'yyyy-MM-dd HH:mm:ss')
            if date.isValid():
                date.setTimeZone(QTimeZone.utc())
                self.reminder_date.setDateTime(date.toLocalTime())

    def save_reminder(self,update):
        text=self.reminder_title.text().strip()
        if not text: return
        row=self.reminder_table.currentRow()
        if update and row<0: return
        reminder_id=self.reminder_table.item(row,0).data(Qt.ItemDataRole.UserRole) if update else None
        stamp=self.reminder_date.dateTime().toPython().astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        self.database.save_reminder(self.controller.character.id,text,stamp,reminder_id); self.refresh_reminders()

    def delete_reminder(self):
        row=self.reminder_table.currentRow()
        if row>=0:
            self.database.delete_reminder(self.controller.character.id,self.reminder_table.item(row,0).data(Qt.ItemDataRole.UserRole)); self.refresh_reminders()
