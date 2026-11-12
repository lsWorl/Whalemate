from __future__ import annotations

from time import monotonic
from collections import deque
from uuid import uuid4
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu, QStyle, QSystemTrayIcon
from aicompanion.avatar.view import AvatarView
from aicompanion.avatar.model_package import cubism_core_available
from aicompanion.audio.capture import Microphone
from aicompanion.voice.echo import EchoGate
from aicompanion.voice.hotkey import DEFAULT_KEY, key_reader, label as key_label
from aicompanion.characters import choose_renderer
from aicompanion.pet.script import CueStream, Vocab, strip_cues
from aicompanion.pet import interjection
from aicompanion.speech.output import spoken_text
from aicompanion.speech.smart import summary_segments
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
        self.registry=registry
        self.web_avatar=None; self.sprite_avatars={}; self.rig_avatars={}; self.renderer=None; self.model_label=''
        self.animation_avatars={}; self.failed_avatar_resources=set()
        self.tray_hint_shown=False; self.first_token=set()
        # 台词标注：每轮回复一个解析器；开语音时动作挂在句子上，等那句开始播放再执行
        self.vocab=Vocab(); self.cue_streams={}; self.pending_cues={}; self.sentence_cues={}
        self.pet=DesktopPetWindow(self.database)
        self.coordinator=ConversationCoordinator(); self.workers=set(); self.tasks=set()
        self.expression_results={}; self.displayed_attachments=set(); self.history_epoch=0
        self.items={}; self.texts={}; self.proactive_keys={}; self.closing=False; self.exit_requested=False
        self.delivered={}; self.reply_characters={}; self.reply_voice={}; self.reply_status={}
        # 语气词：每轮回复只发一声反应（或再加第一句短话），文字留在气泡
        self.interjections=interjection.load(None); self.readers={}; self.voiced_first=set(); self.reply_expressions={}; self.last_interjection=''
        self.asr_queue=deque()
        self.call_active=False; self.call_epoch=0; self.asr_worker=None; self.memory_worker=None
        self.user_speaking=False; self.asr_failures=0
        self.last_interaction=monotonic(); self.first_history_id=None; self.mood='calm'
        self.speech_client=SpeechClient(settings,translation_source=self._translation_client,
                                        tts_source=lambda:self.controller.router.tts_target(self.controller.character.id))
        self.speech=SpeechOutput(self.speech_client,self); self.microphone=self.qt_microphone=Microphone(self)
        # P4：外放打断。回声门控的耦合系数跨会话保存；Chromium 回声消除后端按需创建
        self.web_microphone=None; self.voice_stats={'barge_ins':0,'cancelled':0,'latencies':[]}
        self.echo_gate=EchoGate(coupling=float(self.database.get_setting('echo_coupling',0.6) or 0.6),calibrated=self.database.get_setting('echo_coupling') is not None)
        self.sentences=SentenceBuffer(); self.proactive=ProactiveMessageService(self.database)
        # P3：记忆整理器（衰减 / 遗忘 / 互动事件）与目的机制（心里惦记的事 → 主动开口）
        self.organizer=MemoryOrganizer(self.database); self.purposes=PurposeEngine(self.database)
        from aicompanion.brain.scheduler import ProactiveScheduler
        from aicompanion.brain.life import LifeService
        self.life=LifeService(self.database); self.life_worker=None   # 功能四：她自己的生活（按兴趣去看点东西，值得说的来分享）
        self.outreach_scheduler=ProactiveScheduler(self.database,self.purposes,self.proactive,life=self.life)
        self.purpose_ids={}; self.purpose_worker=None; self.scheduler_ticks=0; self.outreaches={}
        self.outreach_valid={}; self.withdrawn=set()   # 桌面主动消息：第一处可见输出前还该不该说；撤回了的轮次（Codex PR #48 第三轮）
        # P5：词元按用途记账、每日额度；干活（agent）会话
        meter.set_recorder(meter.database_recorder(self.database)); self.quota=meter.Quota(self.database)
        self.agent=AgentSession(self); self.pending_tasks={}
        self.leave_reasons = {}
        self.departure = None
        self.exit_prepared = False
        self.scheduled_offers = {}
        self.open_offer = None
        self._build_layout(); self._build_tray(); self._connect_pet(); self._load_history(); self._apply_settings()
        self.pump=QTimer(self); self.pump.setInterval(100); self.pump.timeout.connect(self._pump); self.pump.timeout.connect(self._refresh_reply_actions); self.pump.start()
        self.pump.timeout.connect(self._poll_self_exit)
        self.scheduler=QTimer(self); self.scheduler.setInterval(60000); self.scheduler.timeout.connect(self._proactive); self.scheduler.start()
        self.speech.subtitle.connect(self._speech_subtitle)
        self.speech.mouth.connect(lambda value:self.avatar.set_state(self.mood,value))
        self.speech.speaking.connect(self._speaking)
        self.speech.error.connect(self._speech_error)
        self.speech.fallback.connect(lambda message:self._status(message))   # 云端声线不可用时退回本地朗读，回复照常
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

    def _voice_mode(self):
        from aicompanion.voice.turns import DEFAULT_MODE
        return self.database.get_setting('voice_mode',DEFAULT_MODE)

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
        from aicompanion.ui.chat_panel import build
        build(self)

    def _apply_backdrop(self):
        """面板背景：设置里选的图，默认 data/private-art/panel-background.png（本机素材，不提交）。"""
        from pathlib import Path
        from aicompanion.ui.chat_view import avatar_from
        path=self.database.get_setting('panel_background','') or str(Path(self.database.database_path).parent/'private-art'/'panel-background.png')
        opacity=float(self.database.get_setting('panel_background_opacity',0.16) or 0)
        self.backdrop.set_image(path,opacity)
        from aicompanion.ui.icons import app_icon
        avatar = avatar_from(path, 42) or app_icon().pixmap(42, 42)
        self.avatar_label.setPixmap(avatar)
        self.chat_list.set_avatar(avatar)

    def _build_tray(self):
        from aicompanion.ui.icons import app_icon
        icon=app_icon()
        if icon.isNull(): icon=self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon); self.tray=QSystemTrayIcon(icon,self); self.tray.setToolTip(self.settings.app_name)
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
        pet.settings_requested.connect(lambda:self._settings()); pet.message_submitted.connect(self.submit)
        pet.character_requested.connect(self._switch_to)
        pet.visibility_changed.connect(lambda visible:self.pet_action.setText('隐藏角色' if visible else '召回角色'))
        pet.appearance_changed.connect(self._apply_avatar)
        pet.interacted.connect(lambda kind:self.database.add_inbox(self.controller.character.id,kind))
        pet.agent_requested.connect(self._ask_agent_goal); pet.agent_stop_requested.connect(self._stop_agent)

    def _refresh_roles(self):
        characters = self.registry.characters()
        old_menu = self.name_label.menu()
        menu = None
        if len(characters) > 1:
            menu = QMenu(self.name_label)
            for character in characters:
                action = menu.addAction(character.name)
                action.setCheckable(True)
                action.setChecked(character.id == self.controller.character.id)
                action.triggered.connect(lambda checked=False, cid=character.id: self._switch_to(cid))
        self.name_label.setMenu(menu)
        self.name_label.setEnabled(menu is not None)
        if old_menu:
            old_menu.deleteLater()

    def _load_history(self,older=False):
        if not older: self.chat_list.clear(); self.first_history_id=None; self.history_epoch+=1
        rows=self.database.recent_messages(30,self.controller.character.id,self.first_history_id if older else None)
        self.older.setVisible(len(rows) == 30)
        if rows: self.first_history_id=rows[0]['id']
        from aicompanion.expression.segments import split_segments
        position=0   # 一条记录可能显示成几个气泡；加载更早记录时按实际气泡数往下排
        for row in rows:
            sender='你' if row['role']=='user' else self.controller.character.name
            note={'cancelled':'（被打断）','failed':'（没说完）','interrupted':'（中断了）'}.get(row['status'],'' if row['status']=='complete' else f"（{row['status']}）")
            content=strip_cues(row['content'],self.vocab)
            if row['source']=='agent' and content.startswith('[干活]'): content='办事 · '+content[4:].lstrip()
            attachments=self.controller.expression.history(row['turn_id'],self.controller.character.id) if row['role']=='assistant' and row['turn_id'] else []
            parts=(split_segments(content) if row['role']=='assistant' and row['source']!='agent' else [content]) or ([''] if not attachments else [])
            if note: parts=(parts[:-1]+[parts[-1]+'\n'+note]) if parts and parts[-1] else parts+[note]
            slots=[]   # (handle, attachment)
            for attachment in attachments[:1]:
                if attachment['placement']=='before_text':
                    handle=self.chat_list.append(sender,'',position if older else None, timestamp=row['created_at']);position+=1
                    handle.row.setVisible(False);slots.append((handle,attachment))
            for part in parts:
                self.chat_list.append(sender,part,position if older else None, timestamp=row['created_at']);position+=1
            for attachment in attachments[:1]:
                if attachment['placement']!='before_text':
                    handle=self.chat_list.append(sender,'',position if older else None, timestamp=row['created_at']);position+=1
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
        self.status.setText(text)
        self.status.setVisible(text not in ('就绪', '角色已就绪', '收到回复', '正在思考…'))
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
        # 历史逐帧开关不影响鲸鲸新安装的 Aboutme；其他角色继续使用自己的开关。
        animation_enabled = (pack is not None and pack.renderer_kind == 'spritesheet') or bool(
            self.database.get_setting(f'pet_animation_enabled:{character.id}', True))
        kind,resource=choose_renderer(pack,self.database.get_setting(f'avatar_mode:{character.id}'),
                                      character.model_path,animation_enabled=animation_enabled)
        if kind=='cubism' and not cubism_core_available():
            if not getattr(self,'_cubism_core_warned',False):
                self._cubism_core_warned=True
                self._status('导入的 Live2D 模型需要 Live2D Cubism Core，本项目不附带，已改用角色默认形象。获取方法见 docs/live2d_model_sources.md。')
            kind,resource=choose_renderer(pack,None,'',animation_enabled=animation_enabled)
        if kind == 'spritesheet':
            if self._show_pack_view(kind, resource, pack):
                return
            kind, resource = choose_renderer(pack, None, '', animation_enabled=False)
        if kind in ('sprites', 'rig'):
            prefer_sprites = kind == 'sprites' and not self.database.get_setting('rig_enabled', True)
            if prefer_sprites and self._show_pack_view('sprites', resource, pack):
                return
            if pack.renderer.get('rig'):
                rig = self._rig_path(pack)
                if rig and self._show_pack_view('rig', rig, pack):
                    return
            if kind == 'sprites' and not prefer_sprites and self._show_pack_view('sprites', resource, pack):
                return
            kind, resource = 'placeholder', None

        if (kind, str(resource)) == self.renderer:
            return
        if kind == 'cubism':
            if self.web_avatar is None:
                self.web_avatar=AvatarView(); self.web_avatar.bridge.loaded.connect(self._avatar_loaded)
            self.pet.set_view(self.web_avatar,'webengine',pack)
            if not self.web_avatar.set_model(resource): self._status('Live2D 模型加载失败，请在设置中重新选择。')
        else:
            hint = '未设置形象\n右键 → 设置'
            if pack and pack.id == 'deepseek-whale':
                hint = '鲸鲸\n请检查角色素材'
            self.pet.set_view(PlaceholderAvatar(hint), 'placeholder', pack)
        self.renderer = (kind, str(resource))
        self.avatar.set_state(self.mood)

    def _show_pack_view(self, kind, path, pack):
        """同一份坏素材在本次运行中只尝试一次，继续尝试角色的下一级形象。"""
        key = str(path)
        if key in self.failed_avatar_resources:
            return False
        if self.renderer == (kind, key):
            return True
        try:
            if kind == 'spritesheet':
                from aicompanion.ui.animation_view import AnimationAvatar
                view = self.animation_avatars.get(key)
                if view is None:
                    view = AnimationAvatar(path)
                    view.failed.connect(lambda message: self._view_failed(view, key, message))
                    self.animation_avatars[key] = view
            elif kind == 'rig':
                from aicompanion.body.rig_view import RigAvatar
                view = self.rig_avatars.get(key)
                if view is None:
                    view = RigAvatar(path)
                    view.bridge.loaded.connect(lambda message: self._rig_loaded(view, key, message))
                    self.rig_avatars[key] = view
            else:
                view = self.sprite_avatars.get(key)
                if view is None:
                    view = SpriteAvatar(path)
                    self.sprite_avatars[key] = view
            self.pet.set_view(view, kind, pack)
        except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as error:
            self.failed_avatar_resources.add(key)
            self._status('角色素材加载失败：' + str(error))
            return False
        self.renderer = (kind, key)
        self.avatar.set_state(self.mood)
        return True

    def _rig_loaded(self, view, key, message):
        if message.startswith('error:'):
            self._view_failed(view, key, message[6:])
        elif self.pet.view is view:
            self._avatar_loaded(message)

    def _view_failed(self, view, key, message):
        self.failed_avatar_resources.add(key)
        if self.pet.view is view and not self.closing:
            self._status('角色素材解码失败：' + message)
            self.renderer = None
            self._apply_avatar()

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
            key = str(path)
            if key in self.failed_avatar_resources or not path.is_file():
                continue
            if key in self.rig_avatars:
                return path
            try:
                load_rig(path)
                return path
            except (RigImportError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                self.failed_avatar_resources.add(key)
                self._status(f'形象包 {path.parent.name} 有问题：{error}')
        if rig.get('source')=='pack': return None   # 只用自己的形象包的角色，不去找 Coopanion
        root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[3]
        try:
            return ensure_whale_rig(root,self.database.get_setting('coopanion_path','') or '',self.database.get_setting('rig_scheme','deepseek'))
        except (RigImportError,OSError,ValueError,KeyError) as error:
            self._status('无法从 Coopanion 导入大肥鱼：'+str(error)); return None

    def _apply_identity(self):
        character=self.controller.character
        # 只更新显示；每轮生成时再按角色和用途取模型（计划书 v4 功能一），不改共享状态
        label=self.controller.router.label(character.id)
        self.model_label=label
        self.mood = self.database.load_emotion(character.id).mood
        self.name_label.setText(character.name)
        self._agent_mode_changed(self.agent_toggle.isChecked())
        self.vocab=Vocab.from_pack(self.registry.pack(character.id)); self.controller.vocabs[character.id]=self.vocab
        self._refresh_setup_banner()
        pack=self.registry.pack(character.id); self.interjections=interjection.load(pack.root if pack else None)
        self.pet.set_identity(character.name,label,[(c.id,c.name) for c in self.registry.characters()],character.id)

    def _avatar_loaded(self, value):
        label = {'ready': '角色已就绪'}
        self._status(label.get(value, value))

    def _send_current_message(self):
        text=self.input_box.text().strip()
        if not text: return
        self.input_box.clear()
        if self.agent_toggle.isChecked():
            # 一次一件事：发出任何一句后都回到聊天（Codex PR #33 P2：手写了 /做 前缀时也要关，否则下一句“谢谢”会变成任务）。
            # 停止词不补前缀，任务忙时打开“办事”说“停”也照样停下。
            self.agent_toggle.setChecked(False)
            if not text.startswith(self.AGENT_PREFIXES) and text.rstrip('！!。.') not in self.STOP_WORDS: text='/做 '+text
        self.submit(text)

    def _agent_mode_changed(self,on):
        self.mode_hint.setVisible(on)
        self.input_box.setPlaceholderText('要她做什么？' if on else f'和{self.controller.character.name}说点什么…')
        self.input_box.setToolTip('例如：把桌面上的截图整理到一个文件夹（改动文件前会先问你）' if on else '点左边的“办事”，这句话就交给她动手做')

    def _refresh_setup_banner(self):
        """聊天模型没配置时显示引导条。"""
        ready = self.controller.router.configured('chat', self.controller.character.id)
        self.model_ready = ready
        self._refresh_presence()
        self.setup_text.setText('现在可以离线试聊。连上模型，她就能陪你聊天了。')
        self.setup_banner.setVisible(not ready)

    def _refresh_presence(self):
        from aicompanion.domain.mood import LABELS
        text = '在线 · 心情' + LABELS.get(self.mood, '平静') if self.model_ready else '还没连上模型'
        color = '#2FA866' if self.model_ready else '#8A4A00'
        if self.model_ready and self.controller.router.offline:
            text, color = '离线试用', '#8A4A00'
        self.presence.setText(f'<span style="color:{color}">●</span> {text}')
        self.pet.presence = text

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
        try:
            from aicompanion.services.mood_service import MoodService
            self.mood=MoodService(self.database).current(self.controller.character.id).label()   # 先回落再读（功能三）
        except Exception: self.mood=self.database.load_emotion(self.controller.character.id).mood
        # 这句话让她变开心（例如被夸）时，回复结束后跳一下庆祝。
        self._refresh_presence()
        self.celebrate_next=self.mood=='happy' and previous_mood!='happy'
        self.pet.dispatch('message_sent')
        self.speech.mood=self.database.get_setting(f'qwen_mood:{self.controller.character.id}','auto')
        if self.speech.mood=='auto': self.speech.mood=self.mood
        self.avatar.set_state(self.mood); self._status('正在听你说…')

    def interrupt(self,checked=False,clear_pending=True):
        self.departure = None
        old=self.coordinator.active
        self.coordinator.cancel(clear_pending)
        for worker in self.workers: worker.cancellation.cancel()
        # 已经完成（或已失败、已取消）的那一轮不再追改成“被打断”：下一句话、挂断通话、退出都会走到这里，
        # 那时上一轮早就说完了（Codex 在 PR #38 实机发现的基线问题，V4-8 先修）。只停掉还在念的语音。
        finished=self.reply_status.get(old) in ('complete','failed','cancelled','suppressed')
        if old in self.items and not finished: self.items[old].mark('[已打断]')
        if old and not finished:self.controller.expression.cancel(old,self.reply_characters.get(old,self.controller.character.id))
        self.speech.stop(); self.sentences=SentenceBuffer(); self.subtitle.clear(); self.subtitle.hide()
        self._pet_subtitle(''); self.pet.dispatch('interrupted')
        self.pet.hide_activity(); self.pet.finish_reply(); self.sentence_cues.clear()
        if old and old in self.reply_characters and not finished:
            self.reply_status[old]='cancelled'; self._sync_delivery(old)
        self._status('已打断' if not self.call_active else '正在听…')

    def _refresh_reply_actions(self):
        self.pet.reply_active = bool(self.workers or self.speech.busy)
        self.stop_button.setVisible(self.pet.reply_active or self.agent.busy)

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
        self.speech_sentences=[]   # 口播可能在末尾。先记正文句子和动作，等同一轮结束后再选择朗读内容。
        if not hasattr(self,'reply_queries'): self.reply_queries={}
        self.reply_queries[generation]=query   # 识别到要动手时，把用户原话交给干活的她
        # 只有用户自己发起的这一轮可以接 <task> 去办事。主动开口（分享网页、心事、问候、追问）的轮次一律丢掉这个标记：
        # 主动消息的提示里带着网页等外来内容，模型复述出来的标记不能变成办事（Codex PR #48 第 1 项）
        if not hasattr(self,'task_turns'): self.task_turns=set()
        if not proactive: self.task_turns.add(generation)
        self.delivered[generation]=''; self.reply_characters[generation]=self.controller.character.id
        self.reply_voice[generation]=self._full_voice(); self.reply_status[generation]='generating'
        if self.reply_voice[generation] and self._speech_style()=='smart':
            from aicompanion.speech.smart import SmartReader
            self.readers[generation]=SmartReader()
        self.reply_expressions[generation]=[]
        # 多段回复显示成几个连续气泡、表情包单独一个气泡（见 SegmentedReply）
        self.items[generation]=self.chat_list.reply(self.controller.character.name,'正在想…')
        # 调度器派发的桌面轮次由本窗口确认送达，普通聊天仍在生成结束时结算。
        worker=ChatWorker(self.controller,generation,self.controller.character,query,proactive,self,
                          generation not in getattr(self,'outreaches',{}))
        self.workers.add(worker)
        if key: self.proactive_keys[generation]=(self.controller.character.id,key)
        worker.expression_event.connect(self._expression_event); worker.result.connect(self._result)
        worker.finished.connect(lambda w=worker:self._worker_finished(w))
        worker.start(); self._status('正在回应…'); self.pet.dispatch('message_sent')
        self.cue_streams[generation]=CueStream(self.vocab); self.pending_cues[generation]=[]
        self.pet.show_activity('thinking')

    def _expression_event(self,generation,payload):
        if not self.coordinator.accepts(generation) or self.closing:return
        if payload[0]=='text':
            stream=getattr(self,'cue_streams',{}).get(generation)
            text,cues=stream.feed(payload[1]) if stream else (payload[1],[])
            if not self._still_wanted(generation,visible=bool(text.strip())):return
            if cues:self._handle_cues(generation,cues)
            if text:self._token(generation,text)
            return
        _,event,media=payload
        if event.generation_id!=generation:return
        if not self._still_wanted(generation,visible=False):return
        if event.kind=='attachment_ready':self._display_attachment(generation,event.attachment,media)
        elif event.kind=='final':
            self.expression_results[generation]=(event.result,media,event.spoken_summary)
            if event.leave_reason:
                self.leave_reasons[generation] = event.leave_reason

    def _still_wanted(self,generation,visible):
        """桌面主动消息在第一处可见输出（第一个字、第一张图）之前，再问一次还该不该说：分享正在生成时关掉了“自己的生活”，
        就撤回这一轮——不显示、不朗读、从聊天记录拿掉、不记为送到（Codex PR #48 第三轮）。已经显示出来的不追回。"""
        check=getattr(self,'outreach_valid',{}).get(generation)
        if check is None:return True
        try:ok=bool(check())
        except Exception:ok=False
        if ok:
            if visible:self.outreach_valid.pop(generation,None)   # 这次就要显示了：之后不再撤回
            return True
        self._withdraw(generation);return False

    def _withdraw(self,generation):
        self.outreach_valid.pop(generation,None); self.withdrawn.add(generation)
        for worker in list(self.workers):
            if getattr(worker,'generation_id',None)==generation:worker.cancellation.cancel()
        if self.coordinator.active==generation:self.coordinator.cancel(False)
        item=self.items.get(generation)
        if item is not None and hasattr(item,'withdraw'):item.withdraw()
        self.controller.expression.cancel(generation,self.reply_characters.get(generation,self.controller.character.id))
        self.pet.hide_activity()

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
            self.outreach_valid.pop(generation,None)  # 安装到气泡成功才算可见，收到附件事件时仍可能解码失败。
            self.controller.expression.receipt(attachment.delivery_id,'displayed')
            if self.pet.isVisible() and self.database.get_setting('pet_bubble_enabled',True):
                self.pet.bubble.say_image(*media,self.pet._head_point(),key='expression:'+generation)
            self.pet.hide_activity()
        else:self.controller.expression.receipt(attachment.delivery_id,'failed')

    def _token(self,generation,token):
        if not self.coordinator.accepts(generation) or self.closing: return
        if generation not in self.first_token:
            self.first_token.add(generation); self.pet.dispatch('reply_token'); self.pet.hide_activity()
        self.texts[generation]+=token
        self.items[generation].set_text(self.texts[generation]); self.chat_list.scrollToBottom()
        self._stage_sentences(generation, self.sentences.feed(token))
        from aicompanion.expression.segments import split_segments
        self._pet_subtitle((split_segments(self.texts[generation]) or [''])[-1][-180:])

    def _result(self,generation,state,error):
        # final 与 result 是两个排队的 Qt 信号，它们之间也可以关闭分享。
        if generation in getattr(self,'outreach_valid',{}) and self.coordinator.accepts(generation):
            self._still_wanted(generation,visible=False)
        expression,media,summary=self.expression_results.pop(generation,(None,None,''))
        reason = getattr(self, 'leave_reasons', {}).pop(generation, '')
        task_turns=getattr(self,'task_turns',set())
        key=self.proactive_keys.pop(generation,None)
        if key and state!='complete': self.database.release_proactive(*key)
        outreach_done=self.outreaches.pop(generation,None)
        getattr(self,'outreach_valid',{}).pop(generation,None)
        withdrawn=generation in getattr(self,'withdrawn',()); getattr(self,'withdrawn',set()).discard(generation)
        if withdrawn: state='cancelled'   # 撤回的主动消息：不算说过
        purpose_id=self.purpose_ids.pop(generation,None)
        if purpose_id:
            if state=='complete': self.purposes.delivered(purpose_id)
            else: self.purposes.release(purpose_id)
        self.reply_status[generation]=state
        self._sync_delivery(generation); self.first_token.discard(generation)
        stream=getattr(self,'cue_streams',{}).pop(generation,None)
        if withdrawn or not self.coordinator.accepts(generation) or self.closing:
            task_turns.discard(generation)
            getattr(self,'pending_tasks',{}).pop(generation,None)
            character_id=self.reply_characters.get(generation,self.controller.character.id)
            if withdrawn or (outreach_done and not self.texts.get(generation,'').strip()
                             and not self.controller.expression.history(generation,character_id)):
                self.database.drop_reply(generation,character_id)
            # 退出事务已结算占位（含已经显示了一部分的消息）。迟到的取消不能把它改成可重发。
            if outreach_done and not self.closing: outreach_done(False,'desktop')
            return
        self.pet.hide_activity()
        if stream and state=='complete':
            rest,cues=stream.flush(); self._handle_cues(generation,cues)
            if rest:
                self.texts[generation]+=rest; self.items[generation].set_text(self.texts[generation])
                self._stage_sentences(generation, self.sentences.feed(rest))
        # 落单反引号后面的委托可能到 flush 才能辨认，这时仍须核实是用户发起的轮次。
        allowed_task=generation in task_turns
        task_turns.discard(generation)
        if state=='complete' and expression and expression.status=='complete':
            for attachment in expression.attachments:self._display_attachment(generation,attachment,media)
            if expression.attachments and not self.texts.get(generation,'').strip() and not any(a.delivery_id in self.displayed_attachments for a in expression.attachments):
                state='failed';error='表情未能显示';self.reply_status[generation]='failed';self._sync_delivery(generation)
                if purpose_id:self.purposes.release(purpose_id)
                if key:self.database.release_proactive(*key)
                if outreach_done:outreach_done(False,'desktop');outreach_done=None
        if outreach_done:
            seen=bool(self.texts[generation].strip() or (expression and any(
                a.delivery_id in self.displayed_attachments for a in expression.attachments)))
            outreach_done(state=='complete' and seen,'desktop')
            if state=='complete' and not seen:
                self.items[generation].withdraw()
                self.database.drop_reply(generation,self.reply_characters[generation])
        if state=='complete':
            self.pet.dispatch('reply_finished',ok=True,celebrate=getattr(self,'celebrate_next',False)); self.celebrate_next=False
            self._stage_sentences(generation, self.sentences.feed('',True))
            self._play_reply(generation, summary)
            # 没能挂到句子上的标注（语音关闭、或标注在最后一句之后）现在执行
            for cue in self.pending_cues.pop(generation,[]): self.pet.perform(cue)
            if not self.reply_voice.get(generation): self.pet.finish_reply()
            if generation not in self.voiced_first and self.texts.get(generation) and not (reason and self.reply_voice.get(generation)):
                self._voice_reaction(generation,self.texts[generation])
            self.voiced_first.discard(generation); self.reply_expressions.pop(generation,None)
            self.database.record_usage(self.controller.character.id,'reply_characters',len(self.texts[generation]))
            task=self.pending_tasks.pop(generation,None)
            if task and allowed_task:
                original=getattr(self,'reply_queries',{}).get(generation,'').strip()
                goal=original if original else task   # 用原话，不用陪伴回复里的转述（转述会丢细节、擅自加步骤）
                self.scheduled_offers[generation] = (self.controller.character.id, goal)
                QTimer.singleShot(400, lambda: self._dispatch_offer(generation))
            state_,note=self.quota.check('companion')
            if state_=='warn': QTimer.singleShot(2500,lambda n=note:self.pet.show_bubble('提醒一下：'+n,8))
            self._status('就绪' if not self.call_active else '通话中 · 正在听')
            if key and self.isHidden() and not self.pet.isVisible(): self.tray.showMessage(self.controller.character.name,self.texts[generation][:120])
            if reason and not task and self._can_self_exit(generation):
                self.departure = (generation, reason, monotonic())
                farewell = self.texts[generation]
                if self.pet.isVisible() and self.database.get_setting('pet_bubble_enabled', True):
                    self.pet.show_bubble(farewell, 6, kind='reply', key='reply')
                elif self.isHidden():
                    self.tray.showMessage(self.controller.character.name, farewell)
        else:
            self.speech.stop()
            self.pet.finish_reply(); self.pending_cues.pop(generation,None); self.pending_tasks.pop(generation,None); self.readers.pop(generation,None)
            if state=='failed': self.pet.dispatch('reply_finished',ok=False,error=error or '回复失败')
            else: self.pet.dispatch('interrupted')
            self.items[generation].mark('[回复未完成]' if self.texts[generation] else '[未能完成回复]')
            self._status(error or '已打断')

    def _can_self_exit(self, generation):
        return (not self.closing and self.coordinator.accepts(generation) and not self.coordinator.pending
                and self.reply_status.get(generation) == 'complete'
                and self.reply_characters.get(generation) == self.controller.character.id
                and not self.call_active and not self.agent.busy and not self.scheduled_offers
                and getattr(self.pet, 'confirm', None) is None and self.database.get_setting('self_exit_enabled', True))

    def _poll_self_exit(self):
        if self.departure is None:
            return
        generation, reason, started = self.departure
        if not self._can_self_exit(generation):
            self.departure = None
            return
        elapsed = monotonic() - started
        if elapsed < 1.5 or (elapsed < 5 and (self.speech.busy or self.speech.queue)):
            return
        self.departure = None
        from aicompanion.brain.self_exit import remember_departure
        try:
            saved = remember_departure(self.database, self.controller.character.id, reason, self.outreach_scheduler)
        except Exception:
            self._status('没能保存这次告别，我先留在这里。')
            return
        if saved:
            self.exit_prepared = True
            self.request_exit()

    def welcome_back(self):
        from aicompanion.brain.self_exit import take_return_greeting
        line = take_return_greeting(self.database, self.controller.character.id)
        if line:
            self.pet.show_bubble(line, 12, kind='reply')
            self._append_message(self.controller.character.name, line)
            self.speak_reply(line)
        with self.database.transaction() as c:
            paused = c.execute("SELECT 1 FROM agent_runs WHERE character_id=? AND status='paused' LIMIT 1",
                               (self.controller.character.id,)).fetchone()
        if paused:
            self._append_message('任务', '有办事任务已暂停。打开设置里的“办事 → 操作日志”，核对做过的步骤后可以继续。')
        return bool(line)

    def _dispatch_offer(self, generation):
        offer = self.scheduled_offers.pop(generation, None)
        if offer and offer[0] == self.controller.character.id:
            self._offer_task(offer[1])

    def _offer_task(self,task):
        if self.closing or self.agent.busy: return
        if self.database.get_setting('agent_auto',True):
            # 不用 /做：聊天里说要做什么，她直接开始；有影响的步骤再逐个征求同意
            self.agent.start(task,announce=False); return
        bubble=self.pet.ask(f'要我现在动手吗？\n「{task}」',[('动手吧','go'),('先不用','no')])
        self.open_offer = (self.controller.character.id, task)
        def answer(value):
            self.open_offer = None
            if value == 'go' and not self.closing:
                self.agent.start(task)
        bubble.chosen.connect(answer)
        self._append_message('提议',task)

    def _handle_cues(self,generation,cues):
        if not cues: return
        self.reply_expressions.setdefault(generation,[]).extend(c.name for c in cues if c.kind=='expression')
        tasks=[c for c in cues if c.kind=='task']
        if tasks:
            if generation in getattr(self,'task_turns',()): self.pending_tasks[generation]=tasks[-1].name
            cues=[c for c in cues if c.kind!='task']
            if not cues: return
        if self.speech.enabled and self.reply_voice.get(generation):
            self.pending_cues.setdefault(generation,[]).extend(cues)   # 等下一句话开始播放
        else:
            for cue in cues: self.pet.perform(cue)

    # ---- 说话方式：语气词 / 语气词 + 第一句 / 完整朗读 ----------------------------------------
    def _speech_style(self):
        style=self.database.get_setting('speech_style',interjection.DEFAULT_STYLE)
        return style if style in interjection.STYLES else interjection.DEFAULT_STYLE

    def _full_voice(self):
        """这一轮是否逐句朗读：完整朗读，或智能朗读（短回复全念、长回复取舍分段，计划书 v4 功能八）。"""
        return self.speech.enabled and self._speech_style() in ('full','smart')

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

    def speak_reply(self,text,expressions=(),spoken_summary=''):
        """干活结果等一次性文字：完整朗读模式念全文，否则只发一声。"""
        if not self.speech.enabled or not text: return
        # 每份结果一个独立的朗读轮次：上一份退回本地朗读 / 出错，不影响下一份（Codex PR #39 第 2 项）
        generation='agent:'+uuid4().hex
        segments = summary_segments(spoken_summary)
        if segments:
            for segment in segments: self.speech.enqueue(segment, 'say:' + generation)
            return
        if self._full_voice() and self._speech_style()=='smart':
            # 办事结果常常很长：同样按智能朗读取舍、分段
            from aicompanion.speech.smart import SmartReader
            reader=SmartReader(); buffer=SentenceBuffer()
            for sentence in buffer.feed(text)+buffer.feed('',True):
                for segment in reader.feed(sentence): self.speech.enqueue(segment,generation)
            for segment in reader.finish(): self.speech.enqueue(segment,generation)
        elif self._full_voice(): self.speech.enqueue(text,generation)
        else:
            key='once:'+uuid4().hex; self._voice_reaction(key,text,list(expressions)); self.voiced_first.discard(key)

    def _speech_subtitle(self,text):
        # 语气词不覆盖气泡里的整段回复
        if str(self.speech.current_id).startswith(('interjection:', 'say:')): return
        self.subtitle.setText(text); self.subtitle.setVisible(bool(text)); self._pet_subtitle(text); self._sentence_started(text)

    def _stage_sentences(self, generation, sentences):
        for sentence in sentences:
            self.speech_sentences.append((sentence, self.pending_cues.pop(generation, [])))

    def _play_reply(self, generation, summary):
        sentences, self.speech_sentences = self.speech_sentences, []
        tail_cues = self.pending_cues.pop(generation, [])
        segments = summary_segments(summary) if self.speech.enabled else []
        if segments:
            self.readers.pop(generation, None)
            self.voiced_first.add(generation)
            for _, cues in sentences:
                for cue in cues: self.pet.perform(cue)
            for segment in segments: self.speech.enqueue(segment, 'say:' + generation)
            self.database.record_usage(self.controller.character.id, 'voice_characters', sum(map(len, segments)))
        else:
            for sentence, cues in sentences:
                self.pending_cues.setdefault(generation, []).extend(cues)
                self._enqueue_sentence(sentence, generation)
            self._finish_reader(generation)
        self.pending_cues.setdefault(generation, []).extend(tail_cues)

    def _enqueue_sentence(self,sentence,generation):
        if not self.reply_voice.get(generation):
            self._voice_reaction(generation,sentence); return
        reader=self.readers.get(generation)
        for segment in (reader.feed(sentence) if reader else [sentence]): self._speak_segment(segment,generation)

    def _finish_reader(self,generation):
        reader=self.readers.pop(generation,None)
        if reader and self.reply_voice.get(generation):
            for segment in reader.finish(): self._speak_segment(segment,generation)
            # 计划书 v4 功能八要对照的数据：每轮念了多少字（用量页可见）
            try: self.database.record_usage(self.controller.character.id,'voice_characters',reader.spoken)
            except Exception: pass

    def _speak_segment(self,segment,generation):
        cues=self.pending_cues.get(generation)
        if cues and self.speech.enabled:
            self.sentence_cues.setdefault(spoken_text(segment),[]).extend(cues); self.pending_cues[generation]=[]
        self.speech.enqueue(segment,generation)

    def _sentence_started(self,text):
        for cue in self.sentence_cues.pop(text,[]): self.pet.perform(cue)

    def _heard(self,generation,text):
        if generation in self.delivered:
            self.delivered[generation]+=text
            self._sync_delivery(generation)

    def _sync_delivery(self,generation):
        if generation not in self.reply_characters: return
        status=self.reply_status.get(generation,'generating')
        # 已完成的回复整段都在气泡里显示过了，按全文记；还在生成 / 被打断的朗读轮次只记已经念出来的部分
        voiced=self.reply_voice.get(generation) and status!='complete'
        text=self.delivered.get(generation,'') if voiced else self.texts.get(generation,'')
        self.database.set_delivered_reply(generation,self.reply_characters[generation],text,status)

    def _worker_finished(self,worker):
        self.workers.discard(worker); worker.deleteLater()
        if not self.closing:
            self._pump()
            if not self.coordinator.pending and self.database.get_setting('memory_analysis',True): self._analyze_memory()
        self._finish_exit_if_ready()

    def _analyze_memory(self):
        character_id=self.controller.character.id
        if self.memory_worker or not self.controller.router.ready('chat',character_id): return
        if self.quota.check_silent('memory')=='stop': return
        client=self.controller.client(character_id,'memory')   # 线程启动前取好，线程里不读共享状态
        worker=TaskWorker(lambda token:self.controller.memory_service.analyze(character_id,client,token),self)
        self.memory_worker=worker; self.tasks.add(worker)
        worker.failed.connect(lambda error:None) # 后台分析失败不破坏主对话。
        worker.finished.connect(lambda w=worker:self._task_finished(w)); worker.start()

    def _task_finished(self,worker):
        self.tasks.discard(worker)
        if worker==self.asr_worker: self.asr_worker=None
        if worker==self.memory_worker: self.memory_worker=None
        if worker==self.purpose_worker: self.purpose_worker=None
        if worker==getattr(self,'life_worker',None): self.life_worker=None
        worker.deleteLater(); self._finish_exit_if_ready()
        if self.call_active and not self.asr_worker and self.asr_queue:
            pcm,rate=self.asr_queue.popleft(); self._start_recognition(pcm,rate)

    def _proactive(self):
        if self.closing or getattr(self, 'departure', None): return
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
        self._maybe_live()
        if self.closing or self.call_active or self.workers or self.coordinator.pending or self.speech.busy: return
        if is_focused(self.database): return   # 专注模式：不主动搭话
        if self.quota.check_silent('companion')=='stop': return
        from aicompanion.ui.desktop_integration import system_idle_seconds
        # 计划书 v4 统一主动调度：纪念日、心事、问候、追问都由调度器决定要不要开口、说哪件（brain/scheduler.py）
        outreach=self.outreach_scheduler.next(character_id,system_idle_seconds(),monotonic()-self.last_interaction)
        if outreach: self._deliver_outreach(character_id,outreach)

    def _deliver_outreach(self,character_id,outreach):
        """把调度器选中的这一件说出去：先占位，桌面说完 / QQ 回执成功再确认，没说成就撤掉、心事放回、问候和纪念日的占位放回。"""
        purpose=outreach.payload.get('purpose')
        if purpose: self.database.update_purpose(purpose['id'],'delivering')
        # 选中就先占位（pending）：走 QQ 要等插件回执，期间不会再选中同一句（Codex PR #46 第 1 项）
        reserved=self.outreach_scheduler.reserve(character_id,outreach)

        def done(result,channel,o=outreach,cid=character_id,rid=reserved):
            # 桌面传 True / False；QQ 传投递结果（发出 / 部分 / 失败 / 未知，bridge/outbox.py）。心事、问候、纪念日的占位一起收尾
            self.outreach_scheduler.settle_delivery(cid,rid,o,result,channel)
        # QQ：放进发件箱时记下轮次（之后按 qq_outbox 对账，重启也接得上）；插件领取、开始发送时记成 delivering
        queued=lambda turn,rid=reserved:self.outreach_scheduler.attach(rid,turn,'qq')
        taken=lambda turn,rid=reserved:self.outreach_scheduler.handed(rid,turn)
        # 交给插件前、插件领取时再问一次还该不该说：分享和追问分享的那句，在关掉“自己的生活”后撤回（Codex PR #48 第 2 项）
        valid=lambda o=outreach:self.outreach_scheduler.still_valid(o)
        if self._proactive_on_qq(outreach.intent,lambda result:done(result,'qq'),taken,queued,valid):return
        generation=self.coordinator.active=uuid4().hex
        self.outreach_scheduler.attach(reserved,generation)   # 追问时按轮次找回这句原话
        def desktop_done(sent,channel):
            from aicompanion.services.companion_dates import CompanionDates
            dates=CompanionDates(self.database)
            if sent: dates.confirm(character_id,generation)
            else: dates.release(character_id,generation)
            done(sent,channel)
        self.outreaches[generation]=desktop_done
        if outreach.source in ('share','date_eve') or outreach.payload.get('origin')=='share': self.outreach_valid[generation]=valid
        self._start(generation,proactive=outreach.intent)

    def _proactive_on_qq(self,intent,done,taken=None,queued=None,still_valid=None):
        """主动消息随机发到 QQ 私聊（用户 2026-11-09）。选中 QQ 就在后台交给桥接生成，插件下次心跳取走发送；
        没生成出来 / 没发出去时 done(False)，问候 / 心事退回去下次再说；插件取件时 taken(轮次)。返回 True 表示这次走 QQ，桌面不再说。"""
        from aicompanion.bridge.server import runtime
        from aicompanion.services.proactive_service import choose_channel
        from aicompanion.ui.desktop_integration import system_idle_seconds
        service=getattr(runtime.server,'service',None) if runtime.server else None
        ready=bool(service and getattr(service,'qq_ready',lambda:False)())
        if choose_channel(self.database,system_idle_seconds(),ready)!='qq':return False
        worker=TaskWorker(lambda token:service.proactive_qq(intent,on_done=done,on_taken=taken,on_queued=queued,still_valid=still_valid),self)
        self.tasks.add(worker)
        worker.succeeded.connect(lambda turn:None if turn else done(False))
        worker.failed.connect(lambda error:done(False))
        worker.finished.connect(lambda w=worker:self._task_finished(w)); worker.start()
        return True

    def _organize_memory(self):
        """每 10 分钟整理一次记忆卡：互动事件 → 小记忆、衰减、遗忘、容量裁剪（本地计算，不调用模型）。"""
        try:
            self.organizer.run(self.controller.character.id)
        except Exception as error:   # 整理失败不影响聊天
            self._status('记忆整理失败：'+str(error))

    def _translation_client(self):
        """日语配音的翻译用当前角色的聊天模型；缓存范围含角色与模型配置修订。没配置时返回 (None, '')。"""
        character_id=self.controller.character.id
        router=self.controller.router
        if not router.ready('chat',character_id): return None,''
        client,resolved=router.client('chat',character_id,'translation')
        return client,f'{character_id}|{resolved.model_id}|{resolved.config_revision}|{resolved.credential_revision}'

    def _maybe_live(self):
        """空闲时按兴趣去看点东西（后台线程，不挡界面）。没配模型或额度用完：照样去看，但不调模型、只记一句（大多不会拿来说）。"""
        character=self.controller.character
        if self.life_worker or self.closing or self.call_active: return
        epoch=self.life.epoch()
        if epoch is None or not self.life.should_start(character.id): return
        search=self.controller.search_factory(character.id)()
        client=None
        if self.controller.router.ready('chat',character.id) and self.quota.check_silent('life')!='stop':
            client=self.controller.client(character.id,'life')
        # 搜索要花点时间，期间别的对话可能把额度用完：真正发起总结请求前再查一次（Codex PR #48 第 4 项）
        allow=lambda:self.quota.check_silent('life')!='stop'
        worker=TaskWorker(lambda token:self.life.run(character.id,search,client,token,character.name,allow_model=allow,epoch=epoch),self)
        self.life_worker=worker; self.tasks.add(worker)
        worker.failed.connect(lambda error:None)
        worker.finished.connect(lambda w=worker:self._task_finished(w)); worker.start()

    def _maybe_generate_purposes(self):
        character_id=self.controller.character.id
        if self.purpose_worker or self.closing or not self.purposes.should_generate(character_id): return
        if not self.controller.router.ready('chat',character_id) or self.quota.check_silent('purpose')=='stop':
            self.purposes.generate(character_id); return   # 离线、没配模型或额度用完：用规则生成，不调用模型
        client=self.controller.client(character_id,'purpose')
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
        self.call_button.setToolTip('结束语音聊天'); self.level.show()
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
        self.interrupt(); self.call_button.setToolTip('语音聊天'); self.level.hide(); self.level.setValue(0)
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
        # 新配置的专属声线只启用一次；之后尊重用户在设置中选择的朗读方式。
        if self.settings.tts_provider=='qwen' and self.settings.qwen_api_key:
            setup=self.settings.qwen_tts_model+':'+self.settings.qwen_tts_voice
            if self.database.get_setting('qwen_voice_setup')!=setup:
                # 只切换朗读方式；朗读开关默认关闭，由用户自己打开（负责人 2026-11-11）
                self.database.set_setting('tts_provider','qwen')
                self.database.set_setting('qwen_voice_setup',setup)
        character_id=self.controller.character.id
        self.speech_client.asr_provider=self.database.get_setting('asr_provider',self.settings.asr_provider)
        # 环境配置只在该角色首次启用时应用；之后尊重设置界面保存的选择。
        if self.settings.speech_language=='ja' and self.database.get_setting(f'speech_language:{character_id}') is None:
            self.database.set_setting(f'speech_language:{character_id}','ja')
        # 负责人 2026-11-11：鲸鲸默认就用有感情的云端声线。只对从没存过朗读方式的库切换一次；
        # 明确选过本地朗读（包括旧库里存着的 system）的不动（Codex PR #41 审查 2）。条件见 adopt_default_voice
        from aicompanion.domain.voice_profile import adopt_default_voice
        adopt_default_voice(self.database,self.controller.router,character_id,self.settings.tts_provider)
        self.speech.provider=self.database.get_setting('tts_provider',self.settings.tts_provider)
        self.speech.language=self.database.get_setting(f'speech_language:{character_id}',self.settings.speech_language)
        self.speech.role_style=self.database.get_setting(f'japanese_style:{character_id}','')
        if self.speech.provider=='breeze':
            # 音色来自“模型与 Key”页（可按角色设置）；cache_id 含音色、模型、参数和 Key 修订，换任何一项都不会命中旧缓存
            target=self.controller.router.tts_target(character_id)
            self.speech.voice=target.cache_id if target else ''
        else:
            self.speech.voice=(self.database.get_setting(f'qwen_voice:{character_id}',self.settings.qwen_tts_voice)
                if self.speech.provider=='qwen' else self.controller.character.voice or self.database.get_setting('tts_voice','tongtong'))
        self.speech.style=self.database.get_setting(f'qwen_style:{character_id}','')
        mood=self.database.get_setting(f'qwen_mood:{character_id}','auto')
        self.speech.mood=self.database.load_emotion(character_id).mood if mood=='auto' else mood
        # 计划书 v4 功能八：朗读默认开启；从没动过开关的库也开启。用户手动关过的保持关闭，只提示一次
        enabled=self.database.get_setting('speech_enabled',True)
        if enabled is False and not self.database.get_setting('speech_default_hint',False):
            self.database.set_setting('speech_default_hint',True)
            QTimer.singleShot(3000,lambda:self.pet.show_bubble('现在可以让我开口说话啦：点聊天面板上的“朗读”就行。',10))
        self.speech.enabled=enabled; self.speak_toggle.setChecked(enabled)
        self._apply_identity(); self._apply_avatar()
        self.controller.agent_enabled=bool(self.database.get_setting('agent_enabled',True))
        self._apply_backdrop()
        # 通话中改了收音设置由 _settings 只重启收音；这里不再挂断重开，否则会打断正在进行的回复

    def _settings(self,page=None):
        # 打开设置不打断正在进行的回复、办事或通话（负责人 2026-11-11 实机反馈）：
        # 生成线程拿的是开始时的角色和模型快照，设置改动只影响下一轮。
        dialog = SettingsWindow(self.controller, self.settings, self, speech_output=self.speech, page=page)
        voice = self._capture_options()

        def apply(section):
            nonlocal voice
            if section in ('identity', 'voice', 'models', 'background', 'agent'):
                self._apply_settings()
                self._refresh_roles()
            if section == 'capture':
                self.speech_client.asr_provider = self.database.get_setting('asr_provider', self.settings.asr_provider)
                current = self._capture_options()
                if self.call_active and current != voice:
                    self.microphone.stop()
                    self._configure_voice()
                    self.microphone.start(self._microphone_device())
                voice = current

        dialog.saved.connect(apply)
        dialog.exec()
        dialog.saved.disconnect(apply)
        self._refresh_roles()
        self._apply_settings()
        reopen = dialog.reopen_wizard
        # 试听和模型测试可以晚于窗口关闭才结束；等该窗口持有的线程结束再释放控件。
        from PySide6.QtCore import QThread
        def release_dialog():
            if any(thread.isRunning() for thread in dialog.findChildren(QThread)):
                QTimer.singleShot(250, release_dialog)
            else:
                dialog.deleteLater()
        release_dialog()
        if reopen:
            QTimer.singleShot(0, lambda: self.open_onboarding(return_to_settings=True))

    def _capture_options(self):
        return (self.database.get_setting('input_device', ''), self.database.get_setting('echo_backend', 'qt'),
                self._voice_mode(), self.database.get_setting('ptt_key', DEFAULT_KEY))

    def open_onboarding(self, return_to_settings=False):
        from aicompanion.ui.onboarding import Onboarding
        wizard = Onboarding(self)
        wizard.exec()
        self._apply_settings()
        page = wizard.destination or ('overview' if return_to_settings else None)
        wizard.deleteLater()
        if page:
            QTimer.singleShot(0, lambda: self._settings(page))

    def _switch_to(self,character_id):
        if not character_id or character_id==self.controller.character.id: return
        character=next((c for c in self.registry.characters() if c.id==character_id),None)
        if character is None: return
        self._end_call(); self.interrupt()
        self.controller.character=character; self.registry.set_active(character_id)
        # 被取消的旧角色任务仍可能排队发送结束信号；保留其送达文本直到收尾。
        self.items.clear(); self._refresh_roles(); self._load_history(); self._apply_settings()
        if not self.welcome_back():
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
        event.ignore()
        if self.closing: return
        offers = list(self.scheduled_offers.values())
        if self.open_offer: offers.append(self.open_offer)
        if not self.exit_prepared:
            def prepare(c):
                visible = {turn for turn in self.outreaches if self.texts.get(turn, '').strip()}
                self.outreach_scheduler.release_on_exit(c, visible)
                self.database.pause_agent_work(self.agent.run_state['run_id'], offers, connection=c)
            try:
                self.database.locked(prepare)
            except Exception:
                self.exit_requested = False
                self._status('未能保存退出状态，请稍后再退出。')
                return
        self.closing=True; self.pump.stop(); self.scheduler.stop()
        self.scheduled_offers.clear()
        self._end_call()
        self.pet.shutdown()
        for view in list(self.sprite_avatars.values())+list(self.rig_avatars.values())+list(self.animation_avatars.values()): view.shutdown()
        if self.web_microphone: self.web_microphone.shutdown()
        self.agent.stop(); self.agent.shutdown()   # 关掉 DeepSeek Harness 运行时
        for worker in self.tasks: worker.cancellation.cancel()
        self.tray.hide(); self._status('正在释放资源…'); self._finish_exit_if_ready()

    def _finish_exit_if_ready(self):
        from PySide6.QtCore import QThread
        if (self.closing and not self.workers and not self.tasks and not self.speech.workers
                and not self.agent.box.working and not any(t.isRunning() for t in self.findChildren(QThread))):
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
        elif self.closing: QTimer.singleShot(100,self._finish_exit_if_ready)
