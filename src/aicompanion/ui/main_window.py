from __future__ import annotations

from time import monotonic
from collections import deque
from uuid import uuid4
from dataclasses import replace
from PySide6.QtCore import QTimer,Qt
from PySide6.QtGui import QAction,QIcon,QPixmap
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QListWidget,QListWidgetItem,QSplitter,QComboBox,QCheckBox,QSystemTrayIcon,QMenu,QStyle,QProgressBar,QFileDialog,QMessageBox)
from aicompanion.avatar.view import AvatarView
from aicompanion.audio.capture import Microphone
from aicompanion.voice.echo import EchoGate
from aicompanion.voice.hotkey import DEFAULT_KEY, key_reader, label as key_label
from aicompanion.characters import choose_renderer, CharacterPackError
from aicompanion.pet.script import CueStream, Vocab, strip_cues
from aicompanion.pet import interjection
from aicompanion.speech.output import spoken_text
from aicompanion.ui.desktop_pet import DesktopPetWindow
from aicompanion.ui.sprite_view import SpriteAvatar, PlaceholderAvatar
from aicompanion.speech.client import SpeechClient
from aicompanion.speech.output import SpeechOutput,SentenceBuffer
from aicompanion.services.conversation_coordinator import ConversationCoordinator
from aicompanion.services.proactive_service import ProactiveMessageService
from aicompanion.brain.focus import is_focused
from aicompanion.brain.memory_card import MemoryOrganizer
from aicompanion.brain.purpose import PurposeEngine
from aicompanion.ui.chat_worker import ChatWorker,TaskWorker
from aicompanion.ui.agent_session import AgentSession
from aicompanion.usage import meter
from aicompanion.ui.settings_window import SettingsWindow
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.personas import apply_emilia_preset


class MainWindow(QMainWindow):
    """聊天面板兼应用协调者：角色常驻在 DesktopPetWindow，本窗口平时隐藏、按需打开。

    Qt 适配器管理显示和设备，协调器管理轮次和过期结果。
    """

    def __init__(self,controller,settings=None,registry=None):
        super().__init__()
        if settings is None:
            from aicompanion.config import load_settings
            settings=load_settings()
        self.controller=controller; self.settings=settings; self.database=controller.database
        if registry is None:
            from aicompanion.characters.registry import CharacterRegistry
            registry=CharacterRegistry(self.database,settings); registry.sync()
            controller.character=registry.active()
        self.registry=registry; self.default_llm_client=controller.llm_client
        self.web_avatar=None; self.sprite_avatars={}; self.rig_avatars={}; self.renderer=None; self.model_label=''
        self.tray_hint_shown=False; self.first_token=set()
        # 台词标注：每轮回复一个解析器；开语音时动作挂在句子上，等那句开始播放再执行
        self.vocab=Vocab(); self.cue_streams={}; self.pending_cues={}; self.sentence_cues={}
        self.pet=DesktopPetWindow(self.database)
        self.coordinator=ConversationCoordinator(); self.workers=set(); self.tasks=set()
        self.expression_results={}; self.displayed_attachments=set(); self.history_epoch=0
        self.items={}; self.texts={}; self.proactive_keys={}; self.closing=False; self.exit_requested=False
        self.delivered={}; self.reply_characters={}; self.reply_voice={}; self.reply_status={}
        # 语气词：每轮回复只发一声反应（或再加第一句短话），文字留在气泡
        self.interjections=interjection.load(None); self.voiced_first=set(); self.reply_expressions={}; self.last_interjection=''
        self.asr_queue=deque()
        self.call_active=False; self.call_epoch=0; self.asr_worker=None; self.memory_worker=None
        self.user_speaking=False; self.asr_failures=0
        self.last_interaction=monotonic(); self.first_history_id=None; self.mood='calm'
        self.speech_client=SpeechClient(settings)
        self.speech=SpeechOutput(self.speech_client,self); self.microphone=self.qt_microphone=Microphone(self)
        # P4：外放打断。回声门控的耦合系数跨会话保存；Chromium 回声消除后端按需创建
        self.web_microphone=None; self.voice_stats={'barge_ins':0,'cancelled':0,'latencies':[]}
        self.echo_gate=EchoGate(coupling=float(self.database.get_setting('echo_coupling',0.6) or 0.6),calibrated=self.database.get_setting('echo_coupling') is not None)
        self.sentences=SentenceBuffer(); self.proactive=ProactiveMessageService(self.database)
        # P3：记忆整理器（衰减 / 遗忘 / 互动事件）与目的机制（心里惦记的事 → 主动开口）
        self.organizer=MemoryOrganizer(self.database); self.purposes=PurposeEngine(self.database)
        self.purpose_ids={}; self.purpose_worker=None; self.scheduler_ticks=0
        # P5：词元按用途记账、每日额度；干活（agent）会话
        meter.set_recorder(meter.database_recorder(self.database)); self.quota=meter.Quota(self.database)
        self.agent=AgentSession(self); self.pending_tasks={}
        self._build_layout(); self._build_tray(); self._connect_pet(); self._load_history(); self._apply_settings()
        self.pump=QTimer(self); self.pump.setInterval(100); self.pump.timeout.connect(self._pump); self.pump.start()
        self.scheduler=QTimer(self); self.scheduler.setInterval(60000); self.scheduler.timeout.connect(self._proactive); self.scheduler.start()
        self.speech.subtitle.connect(self._speech_subtitle)
        self.speech.mouth.connect(lambda value:self.avatar.set_state(self.mood,value))
        self.speech.speaking.connect(self._speaking)
        self.speech.error.connect(self._speech_error)
        self.speech.usage.connect(lambda n:self.database.record_usage(self.controller.character.id,'tts_characters',n))
        self.speech.delivered.connect(self._heard)
        self._connect_microphone(self.qt_microphone)

    def _connect_microphone(self,mic):
        mic.speech_started.connect(self._voice_started)
        mic.utterance.connect(self._transcribe)
        mic.cancelled.connect(self._voice_cancelled)
        mic.error.connect(self._microphone_error)
        mic.level.connect(lambda value:self.level.setValue(int(value*100)))

    def _web_microphone(self):
        if self.web_microphone is None:
            from aicompanion.voice.web_voice import WebMicrophone
            self.web_microphone=WebMicrophone(self); self._connect_microphone(self.web_microphone)
        return self.web_microphone

    def _voice_mode(self): return self.database.get_setting('voice_mode','continuous')

    def _configure_voice(self):
        """按设置选择收音后端和收音方式。Chromium 后端时她的语音也改由同一网页播放，回声消除才有参考。"""
        mode=self._voice_mode(); backend=self.database.get_setting('echo_backend','qt')
        mic=self._web_microphone() if backend=='web' else self.qt_microphone
        if mic is not self.microphone: self.microphone.stop(); self.microphone=mic
        self.speech.set_sink(mic.sink if backend=='web' else None)
        key=key_reader(self.database.get_setting('ptt_key',DEFAULT_KEY)) if mode!='continuous' else None
        mic.configure(mode,self.speech.playback_rms,key,self.echo_gate)

    def _microphone_device(self):
        device_id=self.database.get_setting('input_device','')
        if getattr(self.microphone,'backend','qt')!='web': return device_id
        from PySide6.QtMultimedia import QMediaDevices
        return next((d.description() for d in QMediaDevices.audioInputs() if bytes(d.id()).hex()==device_id),'')

    def _call_hint(self):
        mode=self._voice_mode(); key=key_label(self.database.get_setting('ptt_key',DEFAULT_KEY))
        if mode=='ptt': return f'按住 {key} 说话'
        if mode=='toggle': return f'按一下 {key} 开始说，再按一下结束'
        return '通话中 · 外放可用，直接开口就能打断她'

    @property
    def avatar(self):
        """当前显示角色的视图（立绘、网格动画、Cubism 或占位），都提供 set_state 接口。"""
        return self.pet.view

    @property
    def pet_window(self):
        # 兼容旧代码与冒烟测试：桌宠窗口现在始终存在。
        return self.pet

    def _build_layout(self):
        from aicompanion.ui import theme
        from aicompanion.ui.chat_view import Backdrop, ChatView
        self.setWindowTitle(self.settings.app_name+' · 聊天面板'); self.resize(600,760); self.setMinimumSize(420,520)
        root=Backdrop(); self.backdrop=root; layout=QVBoxLayout(root); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        # 顶栏：头像、名字、模型、状态、角色切换、语音开关、设置
        header=QWidget(); header.setObjectName('Header'); top=QHBoxLayout(header); top.setContentsMargins(16,10,12,10); top.setSpacing(10)
        self.avatar_label=QLabel(); self.avatar_label.setFixedSize(44,44); top.addWidget(self.avatar_label)
        names=QVBoxLayout(); names.setSpacing(0)
        self.name_label=QLabel(self.controller.character.name); self.name_label.setObjectName('Name')
        self.model_info=QLabel(''); self.model_info.setObjectName('Model')
        names.addWidget(self.name_label); names.addWidget(self.model_info); top.addLayout(names); top.addStretch()
        self.status_pill=QLabel('就绪'); self.status_pill.setObjectName('StatusPill'); top.addWidget(self.status_pill)
        self.roles=QComboBox(); self._refresh_roles(); self.roles.currentIndexChanged.connect(self._switch_role); top.addWidget(self.roles)
        self.speak_toggle=QCheckBox('语音'); self.speak_toggle.toggled.connect(self._toggle_speech); top.addWidget(self.speak_toggle)
        settings=QPushButton('设置'); settings.setObjectName('Flat'); settings.clicked.connect(self._settings); top.addWidget(settings)
        layout.addWidget(header)
        older=QPushButton('加载更早记录'); older.setObjectName('Flat'); older.clicked.connect(lambda:self._load_history(older=True))
        layout.addWidget(older,0,Qt.AlignmentFlag.AlignHCenter)
        self.chat_list=ChatView('你'); layout.addWidget(self.chat_list,1)
        self.subtitle=QLabel(''); self.subtitle.setObjectName('Subtitle'); self.subtitle.setWordWrap(True); self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.hide()
        layout.addWidget(self.subtitle)
        # 输入栏：胶囊输入框 + 打断 + 通话 + 发送
        bar=QWidget(); bar.setObjectName('InputBar'); row=QHBoxLayout(bar); row.setContentsMargins(14,5,6,5); row.setSpacing(6)
        self.input_box=QLineEdit(); self.input_box.setObjectName('Input'); self.input_box.setPlaceholderText('说说今天的心情…　（/做 让她动手干活）')
        self.input_box.returnPressed.connect(self._send_current_message); row.addWidget(self.input_box,1)
        stop=QPushButton('■'); stop.setObjectName('Round'); stop.setToolTip('打断她'); stop.clicked.connect(self.interrupt); row.addWidget(stop)
        self.call_button=QPushButton('开启通话'); self.call_button.setObjectName('Round'); self.call_button.clicked.connect(self._toggle_call); row.addWidget(self.call_button)
        send=QPushButton('发送'); send.setObjectName('Send'); send.clicked.connect(self._send_current_message); row.addWidget(send)
        wrap=QVBoxLayout(); wrap.setContentsMargins(14,6,14,4); wrap.setSpacing(4); wrap.addWidget(bar)
        self.level=QProgressBar(); self.level.setObjectName('Level'); self.level.setRange(0,100); self.level.setValue(0); self.level.setTextVisible(False); self.level.setFixedHeight(4)
        wrap.addWidget(self.level); layout.addLayout(wrap)
        self.status=QLabel('就绪'); self.status.setObjectName('Status'); self.status.hide()   # 状态显示在顶栏的小标签里
        layout.addSpacing(8)
        self.setCentralWidget(root)
        self.setStyleSheet(theme.PANEL)

    def _apply_backdrop(self):
        """面板背景：设置里选的图，默认 data/private-art/panel-background.png（本机素材，不提交）。"""
        from pathlib import Path
        from aicompanion.ui.chat_view import avatar_from
        path=self.database.get_setting('panel_background','') or str(Path(self.database.database_path).parent/'private-art'/'panel-background.png')
        opacity=float(self.database.get_setting('panel_background_opacity',0.16) or 0)
        self.backdrop.set_image(path,opacity)
        avatar=avatar_from(path,44)
        if avatar: self.avatar_label.setPixmap(avatar); self.avatar_label.setText('')
        else:
            self.avatar_label.setPixmap(QPixmap()); self.avatar_label.setText(self.controller.character.name[:1])
            self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.avatar_label.setStyleSheet('background:#4D6BFE;color:white;border-radius:22px;font-size:18px;font-weight:600;')

    def _build_tray(self):
        icon=self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon); self.tray=QSystemTrayIcon(icon,self)
        menu=QMenu(self)
        self.pet_action=QAction('隐藏角色',self); self.pet_action.triggered.connect(self._toggle_pet)
        show=QAction('打开聊天面板',self); show.triggered.connect(self._show_chat)
        quit_action=QAction('退出',self); quit_action.triggered.connect(self.request_exit)
        menu.addAction(self.pet_action); menu.addAction(show); menu.addSeparator(); menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        # 单击托盘图标召回角色，双击打开聊天面板。
        def activated(reason):
            if reason==QSystemTrayIcon.ActivationReason.Trigger: self.pet.summon()
            elif reason==QSystemTrayIcon.ActivationReason.DoubleClick: self._show_chat()
        self.tray.activated.connect(activated)
        if QSystemTrayIcon.isSystemTrayAvailable(): self.tray.show()

    def _connect_pet(self):
        pet=self.pet
        pet.chat_requested.connect(self._show_chat); pet.call_requested.connect(self._toggle_call)
        pet.interrupt_requested.connect(self.interrupt); pet.quit_requested.connect(self.request_exit)
        pet.settings_requested.connect(self._settings); pet.message_submitted.connect(self.submit)
        pet.character_requested.connect(self._switch_to)
        pet.visibility_changed.connect(lambda visible:self.pet_action.setText('隐藏角色' if visible else '召回角色'))
        pet.appearance_changed.connect(self._apply_avatar)
        pet.interacted.connect(lambda kind:self.database.add_inbox(self.controller.character.id,kind))
        pet.agent_requested.connect(self._ask_agent_goal); pet.agent_stop_requested.connect(self._stop_agent)

    def _refresh_roles(self):
        self.roles.blockSignals(True); self.roles.clear()
        for character in self.registry.characters(): self.roles.addItem(character.name,character.id)
        self.roles.setCurrentIndex(max(0,self.roles.findData(self.controller.character.id))); self.roles.blockSignals(False)

    def _load_history(self,older=False):
        if not older: self.chat_list.clear(); self.first_history_id=None; self.history_epoch+=1
        rows=self.database.recent_messages(30,self.controller.character.id,self.first_history_id if older else None)
        if rows: self.first_history_id=rows[0]['id']
        from aicompanion.expression.segments import split_segments
        position=0   # 一条记录可能显示成几个气泡；加载更早记录时按实际气泡数往下排
        for row in rows:
            sender='你' if row['role']=='user' else self.controller.character.name
            note={'cancelled':'（被打断）','failed':'（没说完）','interrupted':'（中断了）'}.get(row['status'],'' if row['status']=='complete' else f"（{row['status']}）")
            content=strip_cues(row['content'],self.vocab)
            if row['source']=='agent' and content.startswith('[干活]'): content='🛠 '+content[4:].lstrip()
            attachments=self.controller.expression.history(row['turn_id'],self.controller.character.id) if row['role']=='assistant' and row['turn_id'] else []
            parts=(split_segments(content) if row['role']=='assistant' and row['source']!='agent' else [content]) or ([''] if not attachments else [])
            if note: parts=(parts[:-1]+[parts[-1]+'\n'+note]) if parts and parts[-1] else parts+[note]
            slots=[]   # (handle, attachment)
            for attachment in attachments[:1]:
                if attachment['placement']=='before_text':
                    handle=self.chat_list.append(sender,'',position if older else None);position+=1
                    handle.row.setVisible(False);slots.append((handle,attachment))
            for part in parts:
                self.chat_list.append(sender,part,position if older else None);position+=1
            for attachment in attachments[:1]:
                if attachment['placement']!='before_text':
                    handle=self.chat_list.append(sender,'',position if older else None);position+=1
                    handle.row.setVisible(False);slots.append((handle,attachment))
            for handle,attachment in slots:
                cid=self.controller.character.id; epoch=self.history_epoch
                worker=TaskWorker(lambda token,a=attachment:self.controller.expression.catalog.resolve(a['asset_id'],a['revision'],approved=False),self)
                self.tasks.add(worker)
                def show(media,h=handle,a=attachment,cid=cid,epoch=epoch):
                    if media and cid==self.controller.character.id and epoch==self.history_epoch and not self.closing:
                        h.row.setVisible(True);h.setAttachment(*media,a['placement'])
                worker.succeeded.connect(show)
                worker.finished.connect(lambda w=worker:self._task_finished(w));worker.start()
        if not older: self.chat_list.scrollToBottom()

    def _append_message(self,sender,content):
        return self.chat_list.append(sender,content)

    def _status(self,text):
        self.status.setText(text); self.status_pill.setText(text[:16]+('…' if len(text)>16 else '')); self.status_pill.setToolTip(text)
        self.pet.set_status(text)

    def _pet_subtitle(self,text):
        self.pet.set_subtitle(text)

    def _show_chat(self):
        # 聊天面板贴在角色旁边打开；角色不随面板关闭而消失。
        if self.isHidden():
            pet=self.pet.frameGeometry(); screen=self.pet.screen().availableGeometry() if self.pet.screen() else None
            x=pet.left()-self.width()-12
            if screen and x<screen.left(): x=pet.right()+12
            y=pet.bottom()-self.height()
            if screen: x=max(screen.left(),min(x,screen.right()-self.width())); y=max(screen.top(),min(y,screen.bottom()-self.height()))
            self.move(x,y)
        self.showNormal(); self.raise_(); self.activateWindow(); self.input_box.setFocus()

    def _toggle_pet(self,checked=False):
        if self.pet.isVisible(): self.pet.rest()
        else: self.pet.summon()

    def _apply_avatar(self):
        """根据角色包和设置选择显示方式，并放进常驻桌宠窗口。"""
        character=self.controller.character
        pack=self.registry.pack(character.id)
        kind,resource=choose_renderer(pack,self.database.get_setting(f'avatar_mode:{character.id}'),character.model_path)
        if kind=='sprites' and pack is not None and pack.renderer.get('rig') and self.database.get_setting('rig_enabled',True):
            rig_path=self._rig_path(pack)
            if rig_path: kind,resource='rig',rig_path
        if (kind,str(resource))==self.renderer: return
        view=None
        if kind=='rig':
            try:
                from aicompanion.body.rig_view import RigAvatar
                view=self.rig_avatars.get(str(resource))
                if view is None:
                    view=RigAvatar(resource); view.bridge.loaded.connect(self._avatar_loaded)
                self.rig_avatars[str(resource)]=view
                self.pet.set_view(view,'rig',pack)
            except Exception as error:
                self._status('骨骼形象加载失败，改用立绘：'+str(error)); kind,resource=choose_renderer(pack,None,'')
        if kind=='sprites':
            try:
                view=self.sprite_avatars.get(str(resource)) or SpriteAvatar(resource)
                self.sprite_avatars[str(resource)]=view
                self.pet.set_view(view,'sprites',pack)
            except (CharacterPackError,ValueError) as error:
                self._status('立绘加载失败：'+str(error)); kind='placeholder'
        if kind in ('mesh','png','cubism'):
            if self.web_avatar is None:
                # WebEngine 只在确实需要时创建，鲸娘等立绘角色不占用浏览器内核内存。
                self.web_avatar=AvatarView(); self.web_avatar.bridge.loaded.connect(self._avatar_loaded)
            self.pet.set_view(self.web_avatar,'webengine',pack)
            if kind=='mesh': self.web_avatar.set_animated_emilia() if 'Emilia' in str(resource) else self.web_avatar._load_path(resource)
            elif kind=='png': self.web_avatar.set_preview()
            elif not self.web_avatar.set_model(resource): self._status('Live2D 模型加载失败，请在设置中重新选择。')
        if kind=='placeholder':
            self.pet.set_view(PlaceholderAvatar('未设置形象\n右键 → 设置'),'placeholder',pack)
        self.renderer=(kind,str(resource))
        self.avatar.set_state(self.mood)

    def _rig_path(self,pack):
        """骨骼形象来源，依次：AICOMPANION_RIG 指定的模型（自检、rig_studio 预览）→ 角色包自带的形象包 rig/rig.json
        → 本机 Coopanion 导入的大肥鱼（只限本机）；都没有时返回 None，退回整张立绘。"""
        import os,sys
        from pathlib import Path
        from aicompanion.body.rig_import import ensure_whale_rig, load_rig, RigImportError
        override=os.getenv('AICOMPANION_RIG','')
        candidates=[Path(override)] if override else []
        rig=pack.renderer.get('rig') or {}
        candidates.append(pack.root/(rig.get('path') or 'rig/rig.json'))
        for path in candidates:
            if path.is_file():
                try: load_rig(path); return path
                except (RigImportError,OSError,ValueError,KeyError) as error: self._status(f'形象包 {path.parent.name} 有问题：{error}')
        if rig.get('source')=='pack': return None   # 只用自己的形象包的角色，不去找 Coopanion
        root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[3]
        try:
            return ensure_whale_rig(root,self.database.get_setting('coopanion_path','') or '',self.database.get_setting('rig_scheme','deepseek'))
        except (RigImportError,OSError,ValueError,KeyError) as error:
            self._status('无法从 Coopanion 导入大肥鱼：'+str(error)); return None

    def _apply_identity(self):
        character=self.controller.character
        client,label=self.registry.client_for(character.id,self.default_llm_client)
        self.controller.llm_client=client; self.model_label=label; self.model_info.setText(label)
        self.name_label.setText(character.name)
        self.vocab=Vocab.from_pack(self.registry.pack(character.id)); self.controller.vocabs[character.id]=self.vocab
        pack=self.registry.pack(character.id); self.interjections=interjection.load(pack.root if pack else None)
        self.pet.set_identity(character.name,label,[(c.id,c.name) for c in self.registry.characters()],character.id)

    def _avatar_loaded(self, value):
        label = {'ready': '角色已就绪', 'illustration-ready': '艾米莉娅同人立绘预览已就绪 · 尚未完成 Live2D 绑定',
            'animation-ready': '艾米莉娅 2D 网格动画已就绪（非 Cubism）'}
        self._status(label.get(value, value))

    def _send_current_message(self):
        text=self.input_box.text().strip()
        if text: self.input_box.clear(); self.submit(text)

    AGENT_PREFIXES=('/做','／做','/do ','做件事：')
    STOP_WORDS={'停','停下','停一下','别做了','不用做了','停止'}

    def _ask_agent_goal(self):
        self.pet.open_quick_input(); self.pet.quick.edit.setText('/做 '); self.pet.quick.edit.setPlaceholderText('要我做什么？')

    def _stop_agent(self):
        if self.agent.busy: self.agent.stop(); self._status('正在停下…')

    def submit(self,text,source='text'):
        if self.closing: return
        stripped=text.strip()
        if self.agent.busy and stripped.rstrip('！!。.') in self.STOP_WORDS:
            self._stop_agent(); self.pet.show_user(text); return
        for prefix in self.AGENT_PREFIXES:
            if stripped.startswith(prefix) and stripped[len(prefix):].strip():
                goal=stripped[len(prefix):].strip(); self.pet.show_user(text)
                self.database.add_message('user',text,self.controller.character.id,source=source)
                self.agent.start(goal); return
        self.interrupt(clear_pending=False)
        self.controller.record_user_message(text,source)
        self._append_message('你',text); self.last_interaction=monotonic()
        self.pet.show_user(text)
        self.coordinator.submit(text)
        previous_mood=self.mood
        self.mood=self.database.load_emotion(self.controller.character.id).mood
        # 这句话让她变开心（例如被夸）时，回复结束后跳一下庆祝。
        self.celebrate_next=self.mood=='happy' and previous_mood!='happy'
        self.pet.dispatch('message_sent')
        self.speech.mood=self.database.get_setting(f'qwen_mood:{self.controller.character.id}','auto')
        if self.speech.mood=='auto': self.speech.mood=self.mood
        self.avatar.set_state(self.mood); self._status('正在听你说…')

    def interrupt(self,checked=False,clear_pending=True):
        old=self.coordinator.active
        self.coordinator.cancel(clear_pending)
        for worker in self.workers: worker.cancellation.cancel()
        if old in self.items: self.items[old].mark('[已打断]')
        if old:self.controller.expression.cancel(old,self.reply_characters.get(old,self.controller.character.id))
        self.speech.stop(); self.sentences=SentenceBuffer(); self.subtitle.clear(); self.subtitle.hide()
        self._pet_subtitle(''); self.pet.dispatch('interrupted')
        self.pet.hide_activity(); self.pet.finish_reply(); self.sentence_cues.clear()
        if old and old in self.reply_characters:
            self.reply_status[old]='cancelled'; self._sync_delivery(old)
        self._status('已打断' if not self.call_active else '正在听…')

    def _pump(self):
        if self.closing: return
        # 较早一段识别结果可能在用户接着说话时返回；不要抢在当前发言结束前回应。
        if self.call_active and (self.user_speaking or self.asr_worker or self.asr_queue): return
        if self.coordinator.ready() and not self.workers:
            generation,query=self.coordinator.begin()
            if self.quota.check_silent('companion')=='stop':
                # 今日额度用完：不再请求模型，只用本地台词
                self.pet.dispatch('reply_finished',ok=True); self.pet.hide_activity()
                if not self.pet.say_line('quota_out',force=True): self.pet.show_bubble('今天的词元额度用完啦，我先用本地台词陪你。',8)
                self._append_message(self.controller.character.name,'（今日额度已用完，没有调用模型）'); self._status('今日额度已用完'); return
            self._start(generation,query)

    def _start(self,generation,query='',proactive='',key=None):
        self.sentences=SentenceBuffer(); self.texts[generation]=''
        if not hasattr(self,'reply_queries'): self.reply_queries={}
        self.reply_queries[generation]=query   # 识别到要动手时，把用户原话交给干活的她
        self.delivered[generation]=''; self.reply_characters[generation]=self.controller.character.id
        self.reply_voice[generation]=self._full_voice(); self.reply_status[generation]='generating'
        self.reply_expressions[generation]=[]
        # 多段回复显示成几个连续气泡、表情包单独一个气泡（见 SegmentedReply）
        self.items[generation]=self.chat_list.reply(self.controller.character.name,'正在想…')
        worker=ChatWorker(self.controller,generation,self.controller.character,query,proactive,self)
        self.workers.add(worker)
        if key: self.proactive_keys[generation]=(self.controller.character.id,key)
        worker.expression_event.connect(self._expression_event); worker.result.connect(self._result)
        worker.finished.connect(lambda w=worker:self._worker_finished(w))
        worker.start(); self._status('正在回应…'); self.pet.dispatch('message_sent')
        self.cue_streams[generation]=CueStream(self.vocab); self.pending_cues[generation]=[]
        self.pet.show_activity('thinking')

    def _expression_event(self,generation,payload):
        if not self.coordinator.accepts(generation) or self.closing:return
        if payload[0]=='text':self._token(generation,payload[1]);return
        _,event,media=payload
        if event.generation_id!=generation:return
        if event.kind=='attachment_ready':self._display_attachment(generation,event.attachment,media)
        elif event.kind=='final':self.expression_results[generation]=(event.result,media)

    def _display_attachment(self,generation,attachment,media):
        if not attachment or attachment.delivery_id in self.displayed_attachments:return
        if not self.coordinator.accepts(generation) or self.closing:return
        if not (self.isVisible() or (self.pet.isVisible() and self.database.get_setting('pet_bubble_enabled',True))):
            self.controller.expression.receipt(attachment.delivery_id,'cancelled');return
        catalog=self.controller.expression.catalog;row=catalog.get(attachment.asset_id)
        if (not self.database.get_setting('qq_memes_enabled',True) or not media or not row or not row['enabled'] or row['review_status']!='approved'
                or row['revision']!=attachment.revision or row['root']!=str(catalog.root)):
            self.controller.expression.receipt(attachment.delivery_id,'failed');return
        handle=self.items.get(generation)
        if handle is None:return
        if handle.set_attachment(*media,attachment.placement):
            self.displayed_attachments.add(attachment.delivery_id)
            self.controller.expression.receipt(attachment.delivery_id,'displayed')
            if self.pet.isVisible() and self.database.get_setting('pet_bubble_enabled',True):
                self.pet.bubble.say_image(*media,self.pet._head_point(),key='expression:'+generation)
            self.pet.hide_activity()
        else:self.controller.expression.receipt(attachment.delivery_id,'failed')

    def _token(self,generation,token):
        if not self.coordinator.accepts(generation) or self.closing: return
        if generation not in self.first_token:
            self.first_token.add(generation); self.pet.dispatch('reply_token'); self.pet.hide_activity()
        stream=self.cue_streams.get(generation)
        token,cues=stream.feed(token) if stream else (token,[])
        self._handle_cues(generation,cues)
        if not token: return
        self.texts[generation]+=token
        self.items[generation].set_text(self.texts[generation]); self.chat_list.scrollToBottom()
        for sentence in self.sentences.feed(token): self._enqueue_sentence(sentence,generation)
        if not self.reply_voice.get(generation):
            # 桌宠头顶的气泡跟着显示正在说的那一条
            from aicompanion.expression.segments import split_segments
            self._pet_subtitle((split_segments(self.texts[generation]) or [''])[-1][-180:])

    def _result(self,generation,state,error):
        expression,media=self.expression_results.pop(generation,(None,None))
        key=self.proactive_keys.pop(generation,None)
        if key and state!='complete': self.database.release_proactive(*key)
        purpose_id=self.purpose_ids.pop(generation,None)
        if purpose_id:
            if state=='complete': self.purposes.delivered(purpose_id)
            else: self.purposes.release(purpose_id)
        self.reply_status[generation]=state
        self._sync_delivery(generation); self.first_token.discard(generation)
        if not self.coordinator.accepts(generation) or self.closing: return
        self.pet.hide_activity()
        stream=self.cue_streams.pop(generation,None)
        if stream:
            rest,cues=stream.flush(); self._handle_cues(generation,cues)
            if rest and state=='complete':
                self.texts[generation]+=rest; self.items[generation].set_text(self.texts[generation])
                for sentence in self.sentences.feed(rest): self._enqueue_sentence(sentence,generation)
        if state=='complete' and expression and expression.status=='complete':
            for attachment in expression.attachments:self._display_attachment(generation,attachment,media)
            if expression.attachments and not self.texts.get(generation,'').strip() and not any(a.delivery_id in self.displayed_attachments for a in expression.attachments):
                state='failed';error='表情未能显示';self.reply_status[generation]='failed';self._sync_delivery(generation)
                if purpose_id:self.purposes.release(purpose_id)
                if key:self.database.release_proactive(*key)
        if state=='complete':
            self.pet.dispatch('reply_finished',ok=True,celebrate=getattr(self,'celebrate_next',False)); self.celebrate_next=False
            for sentence in self.sentences.feed('',True): self._enqueue_sentence(sentence,generation)
            # 没能挂到句子上的标注（语音关闭、或标注在最后一句之后）现在执行
            for cue in self.pending_cues.pop(generation,[]): self.pet.perform(cue)
            if not self.reply_voice.get(generation): self.pet.finish_reply()
            if generation not in self.voiced_first and self.texts.get(generation): self._voice_reaction(generation,self.texts[generation])
            self.voiced_first.discard(generation); self.reply_expressions.pop(generation,None)
            self.database.record_usage(self.controller.character.id,'reply_characters',len(self.texts[generation]))
            task=self.pending_tasks.pop(generation,None)
            if task:
                original=getattr(self,'reply_queries',{}).get(generation,'').strip()
                goal=original if original else task   # 用原话，不用陪伴回复里的转述（转述会丢细节、擅自加步骤）
                QTimer.singleShot(400,lambda t=goal:self._offer_task(t))
            state_,note=self.quota.check('companion')
            if state_=='warn': QTimer.singleShot(2500,lambda n=note:self.pet.show_bubble('提醒一下：'+n,8))
            self._status('就绪' if not self.call_active else '通话中 · 正在听')
            if key and self.isHidden() and not self.pet.isVisible(): self.tray.showMessage(self.controller.character.name,self.texts[generation][:120])
        else:
            self.speech.stop()
            self.pet.finish_reply(); self.pending_cues.pop(generation,None); self.pending_tasks.pop(generation,None)
            if state=='failed': self.pet.dispatch('reply_finished',ok=False,error=error or '回复失败')
            else: self.pet.dispatch('interrupted')
            self.items[generation].mark('[回复未完成]' if self.texts[generation] else '[未能完成回复]')
            self._status(error or '已打断')

    def _offer_task(self,task):
        if self.closing or self.agent.busy: return
        if self.database.get_setting('agent_auto',True):
            # 不用 /做：聊天里说要做什么，她直接开始；有影响的步骤再逐个征求同意
            self.agent.start(task,announce=False); return
        bubble=self.pet.ask(f'要我现在动手吗？\n「{task}」',[('动手吧','go'),('先不用','no')])
        bubble.chosen.connect(lambda value,t=task:self.agent.start(t) if value=='go' else None)
        self._append_message('提议',task)

    def _handle_cues(self,generation,cues):
        if not cues: return
        self.reply_expressions.setdefault(generation,[]).extend(c.name for c in cues if c.kind=='expression')
        tasks=[c for c in cues if c.kind=='task']
        if tasks:
            self.pending_tasks[generation]=tasks[-1].name; cues=[c for c in cues if c.kind!='task']
            if not cues: return
        if self.speech.enabled and self.reply_voice.get(generation):
            self.pending_cues.setdefault(generation,[]).extend(cues)   # 等下一句话开始播放
        else:
            for cue in cues: self.pet.perform(cue)

    # ---- 说话方式：语气词 / 语气词 + 第一句 / 完整朗读 ----------------------------------------
    def _speech_style(self):
        style=self.database.get_setting('speech_style','interjection')
        return style if style in interjection.STYLES else 'interjection'

    def _full_voice(self): return self.speech.enabled and self._speech_style()=='full'

    def _voice_reaction(self,generation,sentence,expressions=None):
        """非完整朗读时，一轮回复只在第一句出来时发一声。"""
        if not self.speech.enabled or generation in self.voiced_first: return
        self.voiced_first.add(generation)
        exprs=expressions if expressions is not None else self.reply_expressions.get(generation,[])
        word=interjection.pick(self.interjections,interjection.kind_for(sentence,exprs,self.mood),avoid=self.last_interjection)
        self.last_interjection=word
        self.speech.enqueue(word,'interjection:'+str(generation))
        if self._speech_style()=='brief':
            extra=interjection.brief_text(strip_cues(sentence,self.vocab))
            if extra: self.speech.enqueue(extra,'interjection:'+str(generation))

    def speak_reply(self,text,expressions=()):
        """干活结果等一次性文字：完整朗读模式念全文，否则只发一声。"""
        if not self.speech.enabled or not text: return
        if self._full_voice(): self.speech.enqueue(text,'agent')
        else:
            key='once:'+uuid4().hex; self._voice_reaction(key,text,list(expressions)); self.voiced_first.discard(key)

    def _speech_subtitle(self,text):
        # 语气词不覆盖气泡里的整段回复
        if str(self.speech.current_id).startswith('interjection:'): return
        self.subtitle.setText(text); self.subtitle.setVisible(bool(text)); self._pet_subtitle(text); self._sentence_started(text)

    def _enqueue_sentence(self,sentence,generation):
        if not self.reply_voice.get(generation):
            self._voice_reaction(generation,sentence); return
        cues=self.pending_cues.get(generation)
        if cues and self.speech.enabled:
            self.sentence_cues.setdefault(spoken_text(sentence),[]).extend(cues); self.pending_cues[generation]=[]
        self.speech.enqueue(sentence,generation)

    def _sentence_started(self,text):
        for cue in self.sentence_cues.pop(text,[]): self.pet.perform(cue)

    def _heard(self,generation,text):
        if generation in self.delivered:
            self.delivered[generation]+=text
            self._sync_delivery(generation)

    def _sync_delivery(self,generation):
        if generation not in self.reply_characters: return
        text=self.delivered.get(generation,'') if self.reply_voice.get(generation) else self.texts.get(generation,'')
        status=self.reply_status.get(generation,'generating')
        self.database.set_delivered_reply(generation,self.reply_characters[generation],text,status)

    def _worker_finished(self,worker):
        self.workers.discard(worker); worker.deleteLater()
        if not self.closing:
            self._pump()
            if not self.coordinator.pending and self.database.get_setting('memory_analysis',True): self._analyze_memory()
        self._finish_exit_if_ready()

    def _analyze_memory(self):
        if self.memory_worker or self.settings.llm_provider=='mock': return
        if self.quota.check_silent('memory')=='stop': return
        character_id=self.controller.character.id
        worker=TaskWorker(lambda token:self.controller.memory_service.analyze(character_id,self.controller.llm_client,token),self)
        self.memory_worker=worker; self.tasks.add(worker)
        worker.failed.connect(lambda error:None) # 后台分析失败不破坏主对话。
        worker.finished.connect(lambda w=worker:self._task_finished(w)); worker.start()

    def _task_finished(self,worker):
        self.tasks.discard(worker)
        if worker==self.asr_worker: self.asr_worker=None
        if worker==self.memory_worker: self.memory_worker=None
        if worker==self.purpose_worker: self.purpose_worker=None
        worker.deleteLater(); self._finish_exit_if_ready()
        if self.call_active and not self.asr_worker and self.asr_queue:
            pcm,rate=self.asr_queue.popleft(); self._start_recognition(pcm,rate)

    def _proactive(self):
        character_id=self.controller.character.id
        for reminder in self.database.due_reminders(character_id):
            self._append_message('提醒',reminder['title'])
            if self.pet.isVisible(): self.pet.show_bubble('提醒：'+reminder['title'],15)
            else: self.tray.showMessage('提醒',reminder['title'])
        self.scheduler_ticks+=1
        import sys
        if 'aicompanion.voice.local_asr' in sys.modules: sys.modules['aicompanion.voice.local_asr'].release_if_idle()
        if self.scheduler_ticks%10==1: self._organize_memory()
        self._maybe_generate_purposes()
        if self.closing or self.call_active or self.workers or self.coordinator.pending or self.speech.busy: return
        if is_focused(self.database): return   # 专注模式：不主动搭话
        if self.quota.check_silent('companion')=='stop': return
        from aicompanion.ui.desktop_integration import system_idle_seconds
        purpose=self.purposes.due(character_id,system_idle_seconds(),monotonic()-self.last_interaction)
        if purpose:
            # 心里惦记的事优先于固定的早晚问候
            self.database.update_purpose(purpose['id'],'delivering')
            generation=self.coordinator.active=uuid4().hex
            self.purpose_ids[generation]=purpose['id']
            self._start(generation,proactive=self.purposes.intent(purpose),key=f"purpose:{purpose['id']}")
            return
        due=self.proactive.due(character_id,monotonic()-self.last_interaction)
        if due:
            key,intent=due; self.coordinator.active=uuid4().hex
            self._start(self.coordinator.active,proactive=intent,key=key)

    def _organize_memory(self):
        """每 10 分钟整理一次记忆卡：互动事件 → 小记忆、衰减、遗忘、容量裁剪（本地计算，不调用模型）。"""
        try:
            self.organizer.run(self.controller.character.id)
        except Exception as error:   # 整理失败不影响聊天
            self._status('记忆整理失败：'+str(error))

    def _maybe_generate_purposes(self):
        character_id=self.controller.character.id
        if self.purpose_worker or self.closing or not self.purposes.should_generate(character_id): return
        if self.settings.llm_provider=='mock' or self.quota.check_silent('purpose')=='stop':
            self.purposes.generate(character_id); return   # 离线或额度用完：用规则生成，不调用模型
        client=self.controller.llm_client
        worker=TaskWorker(lambda token:self.purposes.generate(character_id,client,token),self)
        self.purpose_worker=worker; self.tasks.add(worker)
        worker.failed.connect(lambda error:None)
        worker.finished.connect(lambda w=worker:self._task_finished(w)); worker.start()

    def _toggle_speech(self,enabled):
        self.speech.enabled=enabled; self.database.set_setting('speech_enabled',enabled)
        if not enabled:
            self.speech.stop()
            generation=self.coordinator.active
            if generation in self.reply_voice:
                # 关闭朗读后，屏幕上已经显示的回复仍然是有效对话上下文。
                self.reply_voice[generation]=False; self._sync_delivery(generation)

    def _speech_error(self,message):
        generation=self.coordinator.active
        if generation in self.reply_voice:
            self.reply_voice[generation]=False; self._sync_delivery(generation)
        self._status(message)

    def _speaking(self,active):
        if active: self._status('角色正在说话')
        elif not self.speech.queue and not self.workers: self.pet.finish_reply()   # 最后一句播完
        if hasattr(self.avatar,'set_voice_active'): self.avatar.set_voice_active(active)
        self.pet.dispatch('speaking',active=bool(active))

    def _toggle_call(self):
        if self.call_active: self._end_call(); return
        try:
            self.speech_client.ensure_recognition_ready()
            # 先建立通话状态，让麦克风启动时同步报告的设备错误也能正常结束通话。
            self.asr_failures=0; self.user_speaking=False
            self.call_active=True; self.call_epoch+=1
            self._configure_voice()
            self.microphone.start(self._microphone_device())
        except Exception as error:
            if self.call_active: self._end_call()
            self._status(str(error)); return
        if not self.call_active: return
        self.call_button.setText('结束通话')
        self.speak_toggle.setChecked(True); hint=self._call_hint(); self._status(hint)
        self.pet.set_call_active(True)
        if self.pet.isVisible(): self.pet.show_bubble(hint,4)

    def _end_call(self):
        self.call_active=False; self.call_epoch+=1; self.microphone.stop()
        self.user_speaking=False; self.pet.hide_activity()
        if self.echo_gate.calibrated: self.database.set_setting('echo_coupling',round(self.echo_gate.c,4))
        self.speech.set_sink(None)
        self.asr_queue.clear()
        if self.asr_worker: self.asr_worker.cancellation.cancel()
        self.interrupt(); self.call_button.setText('开启通话'); self.level.setValue(0)
        self.pet.set_call_active(False)

    def _microphone_error(self,message): self._end_call(); self._status(message)

    def _voice_started(self):
        if self.call_active:
            barge=self.speech.busy
            self.user_speaking=True
            self.interrupt(clear_pending=False); self._status('正在听…')
            self.pet.show_activity('listening')
            if barge: self.voice_stats['barge_ins']+=1

    def _voice_cancelled(self,reason):
        if not self.call_active: return
        self.user_speaking=False; self.pet.hide_activity(); self.voice_stats['cancelled']+=1
        self._status('没听到内容，'+self._call_hint())

    def _transcribe(self,pcm,rate):
        if not self.call_active: return
        self.user_speaking=False; self.pet.hide_activity()
        if self.asr_worker:
            if len(self.asr_queue)<3: self.asr_queue.append((pcm,rate))
            else: self._status('语音积压，请稍候再说。')
            return
        self._start_recognition(pcm,rate)

    def _start_recognition(self,pcm,rate):
        # 从队列开始识别不代表新一段讲话结束，不能在这里重置 user_speaking。
        epoch=self.call_epoch; character_id=self.controller.character.id
        worker=TaskWorker(lambda token:self.speech_client.transcribe(pcm,rate,token),self)
        self.asr_worker=worker; self.tasks.add(worker)
        kind='asr_local_milliseconds' if self.speech_client.asr_provider=='local' else 'asr_milliseconds'
        self.database.record_usage(character_id,kind,int(len(pcm)/2/rate*1000))
        worker.succeeded.connect(lambda text:self._recognized(epoch,character_id,text))
        worker.failed.connect(lambda error:self._recognition_failed(epoch,character_id,error))
        worker.finished.connect(lambda w=worker:self._task_finished(w)); worker.start(); self._status('正在识别…')

    def _recognized(self,epoch,character_id,text):
        if self.closing or not self.call_active or epoch!=self.call_epoch or character_id!=self.controller.character.id:
            return
        self.asr_failures=0
        if not text or not any(c.isalnum() for c in text):
            self._status('没有听清这句话，可以再说一次。')
            return
        self.submit(text,'voice')
        # 较早结果不能覆盖正在录制的下一段发言的提示。
        if self.user_speaking: self._status('正在听…')

    def _recognition_failed(self,epoch,character_id,error):
        if self.closing or not self.call_active or epoch!=self.call_epoch or character_id!=self.controller.character.id:
            return
        self.asr_failures+=1
        if self.asr_failures>=3:
            self._end_call()
            self._status('语音识别连续失败，通话已暂停：'+error)
        else:
            self._status('语音识别失败，可以再说一次：'+error)

    def _apply_settings(self):
        character=apply_emilia_preset(self.database,self.controller.character,self.settings.character_preset=='emilia')
        if character is not self.controller.character:
            self.controller.character=character
            self._refresh_roles(); self._load_history()
        # 新配置的专属声线只启用一次；之后尊重用户在设置中选择的朗读方式。
        if self.settings.tts_provider=='qwen' and self.settings.qwen_api_key:
            setup=self.settings.qwen_tts_model+':'+self.settings.qwen_tts_voice
            if self.database.get_setting('qwen_voice_setup')!=setup:
                self.database.set_setting('tts_provider','qwen')
                self.database.set_setting('speech_enabled',True)
                self.database.set_setting('qwen_voice_setup',setup)
        character_id=self.controller.character.id
        self.speech_client.asr_provider=self.database.get_setting('asr_provider',self.settings.asr_provider)
        # 环境配置只在该角色首次启用时应用；之后尊重设置界面保存的选择。
        if self.settings.speech_language=='ja' and self.database.get_setting(f'speech_language:{character_id}') is None:
            self.database.set_setting(f'speech_language:{character_id}','ja')
        if character_id==DEFAULT_CHARACTER.id and self.settings.avatar_preview=='emilia' and self.database.get_setting(f'avatar_mode:{character_id}') is None and not self.registry.pack(character_id):
            self.database.set_setting(f'avatar_mode:{character_id}','illustration')
        self.speech.provider=self.database.get_setting('tts_provider',self.settings.tts_provider)
        self.speech.language=self.database.get_setting(f'speech_language:{character_id}',self.settings.speech_language)
        self.speech.role_style=self.database.get_setting(f'japanese_style:{character_id}','')
        self.speech.voice=(self.database.get_setting(f'qwen_voice:{character_id}',self.settings.qwen_tts_voice)
            if self.speech.provider=='qwen' else self.controller.character.voice or self.database.get_setting('tts_voice','tongtong'))
        self.speech.style=self.database.get_setting(f'qwen_style:{character_id}','')
        mood=self.database.get_setting(f'qwen_mood:{character_id}','auto')
        self.speech.mood=self.database.load_emotion(character_id).mood if mood=='auto' else mood
        enabled=self.database.get_setting('speech_enabled',False)
        self.speech.enabled=enabled; self.speak_toggle.setChecked(enabled)
        self._apply_identity(); self._apply_avatar()
        self.controller.agent_enabled=bool(self.database.get_setting('agent_enabled',True))
        self._apply_backdrop()
        if self.call_active:   # 收音方式或回声后端改了：重开一次通话让新设置生效
            self._end_call(); self._toggle_call()

    def _settings(self):
        self._end_call(); self.interrupt()
        dialog=SettingsWindow(self.controller,self.settings,self,speech_output=self.speech); dialog.exec()
        self._refresh_roles(); self._apply_settings()

    def _switch_role(self,index):
        self._switch_to(self.roles.itemData(index))

    def _switch_to(self,character_id):
        if not character_id or character_id==self.controller.character.id: return
        character=next((c for c in self.registry.characters() if c.id==character_id),None)
        if character is None: return
        self._end_call(); self.interrupt()
        self.controller.character=character; self.registry.set_active(character_id)
        # 被取消的旧角色任务仍可能排队发送结束信号；保留其送达文本直到收尾。
        self.items.clear(); self._refresh_roles(); self._load_history(); self._apply_settings()
        self.pet.greet()

    def request_exit(self):
        self.exit_requested=True; self.close()

    def closeEvent(self,event):
        if not self.exit_requested:
            # 关闭聊天面板只是隐藏；角色继续留在桌面上。
            event.ignore(); self.hide()
            if not self.pet.isVisible() and self.tray.isVisible() and not self.tray_hint_shown:
                self.tray_hint_shown=True; self.tray.showMessage('AICompanion','角色在托盘里休息，单击托盘图标可以叫她回来。')
            return
        event.ignore(); self.closing=True; self.pump.stop(); self.scheduler.stop(); self._end_call()
        self.pet.shutdown()
        for view in list(self.sprite_avatars.values())+list(self.rig_avatars.values()): view.shutdown()
        if self.web_microphone: self.web_microphone.shutdown()
        self.agent.stop(); self.agent.shutdown()   # 关掉 DeepSeek Harness 运行时
        for worker in self.tasks: worker.cancellation.cancel()
        self.tray.hide(); self._status('正在释放资源…'); self._finish_exit_if_ready()

    def _finish_exit_if_ready(self):
        if self.closing and not self.workers and not self.tasks and not self.speech.workers:
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
        elif self.closing: QTimer.singleShot(100,self._finish_exit_if_ready)
