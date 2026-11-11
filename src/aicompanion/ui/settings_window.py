from dataclasses import replace
from uuid import uuid4
from datetime import timezone
from PySide6.QtCore import Qt,QDateTime,QTimeZone
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QWidget,QFormLayout,QLineEdit,
    QPlainTextEdit,QPushButton,QCheckBox,QComboBox,QSpinBox,QTableWidget,QTableWidgetItem,QLabel,QFileDialog,QMessageBox,QDateTimeEdit,QScrollArea)
from aicompanion.speech.qwen_client import DEFAULT_STYLE
from aicompanion.avatar.model_package import validate_model_package, ModelPackageError


class SettingsWindow(QDialog):
    """设置窗口。左侧按“她 / 能力 / 连接 / 系统”分组，第一页“概览”告诉用户还差哪几步、去哪里设置。

    页面键（open_page 参数、概览里的“去设置”按钮都用它）：overview、persona、memory、relationship、proactive、
    models、voice、agent、library、platforms、storage、usage。
    """
    PAGES = ('overview', 'persona', 'memory', 'relationship', 'proactive', 'models', 'voice', 'agent', 'library',
             'platforms', 'storage', 'usage')

    def __init__(self,controller,settings,parent=None,speech_output=None,page=None):
        super().__init__(parent)
        from aicompanion.ui.settings_layout import ComingSoon, Page, Section, SettingsNav, scrolled, tabs
        self.controller=controller; self.database=controller.database; self.settings=settings
        self.speech_output=speech_output
        self.setWindowTitle('设置'); self.resize(980,700); self.setMinimumSize(820,560)
        from aicompanion.ui import theme; self.setStyleSheet(theme.DIALOG)
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.nav=SettingsNav(); layout.addWidget(self.nav,1)
        cid=controller.character.id

        # ---- 角色与形象 ---------------------------------------------------------------------
        self.name=QLineEdit(controller.character.name)
        # 计划书 v4 功能二：设定和说话风格属于“人格”，在人格编辑器里改；这里的 description / style 指向编辑器的输入框
        from aicompanion.ui.persona_page import PersonaEditor
        registry=getattr(parent,'registry',None); pack=registry.pack(cid) if registry is not None else None
        self.persona_editor=PersonaEditor(controller,pack.persona if pack else None,self)
        self.description=self.persona_editor.description; self.style=self.persona_editor.style
        self.model=QLineEdit(controller.character.model_path)
        self.avatar_mode=QComboBox()
        self.avatar_mode.addItem('角色包默认形象','sprites')
        self.avatar_mode.addItem('导入的 Live2D 模型','live2d')
        self.avatar_mode.setCurrentIndex(max(0,self.avatar_mode.findData(self.database.get_setting(f'avatar_mode:{cid}','sprites'))))
        choose=QPushButton('选择 Live2D 模型…'); choose.clicked.connect(self.choose_model)
        new=QPushButton('用当前编辑的人格新建一个独立角色（不共享记忆）'); new.clicked.connect(self.create_character)
        self.panel_background=QLineEdit(self.database.get_setting('panel_background',''))
        self.panel_background.setPlaceholderText('留空 = data/private-art/panel-background.png')
        pick_bg=QPushButton('选择图片…'); pick_bg.clicked.connect(self._pick_background)
        bg_row=QHBoxLayout(); bg_row.addWidget(self.panel_background,1); bg_row.addWidget(pick_bg)
        self.panel_opacity=QSpinBox(); self.panel_opacity.setRange(0,100); self.panel_opacity.setSuffix(' %')
        self.panel_opacity.setValue(int(round(float(self.database.get_setting('panel_background_opacity',0.16) or 0)*100)))
        persona=Page('角色与形象','她是谁、怎么想怎么说、长什么样。换人格、改名字都不会影响记忆和关系。')
        who=QFormLayout(); who.addRow('名字',self.name)
        identity=persona.add(Section('她是谁','名字属于她自己，换人格也不会变。',who)); identity.add(new)
        persona.add(Section('人格','同一个她可以有几种性格：新建、复制、切换、导入 SillyTavern 角色卡，每次保存都留一个版本可以回退。'
                                  '“设为当前”后下一句话就用新人格。',self.persona_editor))
        looks=QFormLayout(); looks.addRow('桌面形象',self.avatar_mode); looks.addRow('Live2D 模型',self.model); looks.addRow('',choose)
        persona.add(Section('桌面形象','默认用角色包自带的立绘动画；也可以导入自己的 Live2D 模型（.model3.json）。',looks))
        panel=QFormLayout(); panel.addRow('背景图',bg_row); panel.addRow('背景不透明度',self.panel_opacity)
        persona.add(Section('聊天面板外观','背景图只在本机使用，不会上传或随项目分发。',panel))
        persona.finish()

        # ---- 声音与通话 ---------------------------------------------------------------------
        self.provider=QComboBox()
        for label,value in [('本地朗读（系统自带，不计费）','system'),('智谱语音','cloud'),('千问动漫声线','qwen'),('Breeze Blue（音色在“模型与 Key”页选）','breeze')]: self.provider.addItem(label,value)
        self.provider.setCurrentIndex(max(0,self.provider.findData(self.database.get_setting('tts_provider',settings.tts_provider))))
        self.voice=QComboBox(); self.voice.setEditable(True)
        self._voice_provider_changed()
        self.provider.currentIndexChanged.connect(self._voice_provider_changed)
        self.voice_style=QPlainTextEdit(self.database.get_setting(f'qwen_style:{cid}',DEFAULT_STYLE))
        self.voice_style.setMaximumHeight(74)
        self.voice_mood=QComboBox()
        for label,value in [('跟随角色情绪','auto'),('温柔平静','calm'),('轻快开心','happy'),('含蓄害羞','shy'),('低缓失落','sad'),('轻微生气','angry')]: self.voice_mood.addItem(label,value)
        self.voice_mood.setCurrentIndex(max(0,self.voice_mood.findData(self.database.get_setting(f'qwen_mood:{cid}','auto'))))
        self.speech_language=QComboBox()
        self.speech_language.addItem('中文配音 / 中文字幕','zh')
        self.speech_language.addItem('日语配音 / 中文字幕','ja')
        self.speech_language.setCurrentIndex(max(0,self.speech_language.findData(self.database.get_setting(f'speech_language:{cid}',settings.speech_language))))
        self.japanese_style=QPlainTextEdit(self.database.get_setting(f'japanese_style:{cid}',''))
        self.japanese_style.setMaximumHeight(74)
        preview=QPushButton('试听声线'); preview.clicked.connect(self.preview_voice); preview.setEnabled(speech_output is not None)
        self.preview_status=QLabel(''); self.preview_status.setWordWrap(True); self.preview_status.setObjectName('Hint')
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
        from aicompanion.voice.hotkey import KEYS, DEFAULT_KEY
        from aicompanion.voice.turns import DEFAULT_MODE
        self.voice_mode=QComboBox()
        for text,value in [('按住说话：按住收音键说，松开发送（默认）','ptt'),('切换式：按一下开始，再按一下结束','toggle'),('持续通话：直接开口，可随时打断她','continuous')]: self.voice_mode.addItem(text,value)
        self.voice_mode.setCurrentIndex(max(0,self.voice_mode.findData(self.database.get_setting('voice_mode',DEFAULT_MODE))))
        self.ptt_key=QComboBox()
        for value,(text,_code) in KEYS.items(): self.ptt_key.addItem(text,value)
        self.ptt_key.setCurrentIndex(max(0,self.ptt_key.findData(self.database.get_setting('ptt_key',DEFAULT_KEY))))
        self.echo_backend=QComboBox()
        self.echo_backend.addItem('回声门控（播放时提高门槛，自动校准）','qt')
        self.echo_backend.addItem('浏览器回声消除（Chromium AEC3，播放也走网页）','web')
        self.echo_backend.setCurrentIndex(max(0,self.echo_backend.findData(self.database.get_setting('echo_backend','qt'))))
        self.asr_provider.setCurrentIndex(max(0,self.asr_provider.findData(self.database.get_setting('asr_provider',settings.asr_provider))))
        self.speech_style=QComboBox()
        for text,value in [('只说语气词（嘿嘿、诶？），文字看气泡','interjection'),('语气词 + 第一句短话','brief'),('完整朗读每一句','full')]: self.speech_style.addItem(text,value)
        self.speech_style.setCurrentIndex(max(0,self.speech_style.findData(self.database.get_setting('speech_style','interjection'))))
        voice_page=Page('声音与通话','朗读默认关闭：在聊天面板点“朗读”打开；点“通话”后按住收音键（默认右 Ctrl）和她说话。')
        speak=QFormLayout(); speak.addRow('说多少',self.speech_style); speak.addRow('朗读方式',self.provider); speak.addRow('声线',self.voice)
        speak.addRow('配音语言',self.speech_language)
        tryit=QHBoxLayout(); tryit.addWidget(preview); tryit.addWidget(self.preview_status,1); speak.addRow('',tryit)
        voice_page.add(Section('她怎么说','试听会用下面的设置合成一句话（云端声线会产生少量用量）。',speak))
        tone=QFormLayout(); tone.addRow('语气',self.voice_mood); tone.addRow('表达风格',self.voice_style); tone.addRow('日语口吻',self.japanese_style)
        voice_page.add(Section('语气与风格','只对千问动漫声线和日语配音生效。',tone))
        listen=QFormLayout(); listen.addRow('麦克风',self.input); listen.addRow('收音方式',self.voice_mode); listen.addRow('收音键',self.ptt_key)
        listen.addRow('语音识别',self.asr_provider)
        self.asr_model=QLabel(''); self.asr_model.setWordWrap(True); self.asr_model.setObjectName('Hint'); listen.addRow('识别模型',self.asr_model)
        listen.addRow('外放回声',self.echo_backend)
        self.voice_hint=QLabel(''); self.voice_hint.setWordWrap(True); self.voice_hint.setObjectName('Hint'); listen.addRow('',self.voice_hint)
        voice_page.add(Section('你怎么说','通话中改了这里的设置，保存后只重启收音，不会打断她。',listen))
        voice_page.add(ComingSoon('回复默认带语音','用大肥鱼的声线把回复说出来；长回复挑重点分几段念，代码和清单不照着念。'))
        voice_page.finish()
        self.asr_provider.currentIndexChanged.connect(self._recognition_provider_changed)
        self._recognition_provider_changed()

        # ---- 记忆与心事 ---------------------------------------------------------------------
        self.analysis=QCheckBox('聊天后自动整理长期记忆（会产生少量模型用量）'); self.analysis.setChecked(self.database.get_setting('memory_analysis',True))
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(['类型','记忆','清晰度','范围','更新于'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(1,380); self.table.setMinimumHeight(260)
        buttons=QHBoxLayout(); refresh=QPushButton('刷新'); refresh.clicked.connect(self.refresh)
        delete=QPushButton('删除选中（真的忘掉）'); delete.clicked.connect(self.delete_memory)
        private=QPushButton('设为只在私下用'); private.clicked.connect(lambda:self.set_memory_scope('private'))
        shared=QPushButton('设为共享'); shared.clicked.connect(lambda:self.set_memory_scope('pet'))
        for button in (refresh,delete,private,shared): buttons.addWidget(button)
        buttons.addStretch(1)
        self.archive_label=QLabel(''); self.archive_label.setObjectName('Hint')
        memory_page=Page('记忆与心事','她记得的事和心里惦记的事。记忆在桌面和 QQ 之间共享。')
        cards=Section('记忆卡','记忆会随时间变模糊，低于 30% 移出记忆卡（存档仍保留，再提到会重新记住）。删除 = 真的忘掉，以后不会再被记住；'
                      '标成“只在私下用”的，她不会在群聊和陌生人私聊里用。')
        cards.add(self.analysis); cards.add(self.table); cards.add(buttons); cards.add(self.archive_label); memory_page.add(cards)
        self.purpose_table=QTableWidget(0,4); self.purpose_table.setHorizontalHeaderLabels(['状态','类型','心里惦记的事','计划时间'])
        self.purpose_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.purpose_table.horizontalHeader().setStretchLastSection(True); self.purpose_table.setColumnWidth(2,360); self.purpose_table.setMinimumHeight(180)
        memory_page.add(Section('心事','她会从清楚的记忆里想出想聊的事，在你不忙、非深夜时主动开口；每天最多 3 次。',self.purpose_table))
        # 计划书 v4 功能六（阶段二）：召回与整份请求预算已接入；语义检索和有来源的摘要在阶段四
        from aicompanion.services import budget
        self.context_report=QLabel(budget.describe(self.database.get_setting(f'context_report:{cid}')))
        self.context_report.setWordWrap(True); self.context_report.setObjectName('Hint')
        memory_page.add(Section('想得起旧事','聊到相关的事时，她会想起淡忘的旧记忆、30 条之外更早的聊天、以前帮你办过的事；'
                                '你删掉的记忆不会再想起来，群里的聊天也不会出现在桌面上。每次请求有字数预算，超了先丢最早的聊天记录，'
                                '人格和你刚说的话不会被丢。',self.context_report))
        memory_page.add(ComingSoon('换个说法也想得起','同义词和省略说法也能对上（语义检索）；很早以前的聊天用有来源的摘要代替，需要时能查回原话。'))
        memory_page.finish()

        # ---- 主动与提醒 ---------------------------------------------------------------------
        self.proactive=QCheckBox('允许她早晚问候、主动找你聊天'); self.proactive.setChecked(self.database.get_setting('proactive_enabled',True))
        self.quiet_start=QSpinBox(); self.quiet_start.setRange(0,23); self.quiet_start.setValue(self.database.get_setting('quiet_start',23)); self.quiet_start.setSuffix(' 点')
        self.quiet_end=QSpinBox(); self.quiet_end.setRange(0,23); self.quiet_end.setValue(self.database.get_setting('quiet_end',8)); self.quiet_end.setSuffix(' 点')
        quiet=QHBoxLayout(); quiet.addWidget(QLabel('从')); quiet.addWidget(self.quiet_start); quiet.addWidget(QLabel('到')); quiet.addWidget(self.quiet_end)
        quiet.addWidget(QLabel('不主动打扰')); quiet.addStretch(1)
        proactive_page=Page('主动与提醒','她什么时候可以主动开口，以及你交代给她的提醒。')
        talk=Section('主动聊天',''); talk.add(self.proactive); talk.add(quiet); proactive_page.add(talk)
        self.reminder_table=QTableWidget(0,3); self.reminder_table.setHorizontalHeaderLabels(['内容','时间（UTC）','状态'])
        self.reminder_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.reminder_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows); self.reminder_table.setMinimumHeight(160)
        self.reminder_table.itemSelectionChanged.connect(self.select_reminder)
        self.reminder_title=QLineEdit(); self.reminder_title.setPlaceholderText('例如：提醒我交周报')
        self.reminder_date=QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600)); self.reminder_date.setCalendarPopup(True)
        self.reminder_date.setDisplayFormat('yyyy-MM-dd HH:mm'); rf=QFormLayout()
        rf.addRow('提醒内容',self.reminder_title); rf.addRow('本地时间',self.reminder_date)
        rb=QHBoxLayout(); add=QPushButton('新增'); add.clicked.connect(lambda:self.save_reminder(False)); rb.addWidget(add)
        update=QPushButton('更新选中'); update.clicked.connect(lambda:self.save_reminder(True)); rb.addWidget(update)
        remove=QPushButton('删除选中'); remove.clicked.connect(self.delete_reminder); rb.addWidget(remove); rb.addStretch(1)
        reminders=Section('提醒','也可以直接在聊天里说“明天九点提醒我……”。'); reminders.add(self.reminder_table); reminders.add(rf); reminders.add(rb)
        proactive_page.add(reminders)
        proactive_page.add(ComingSoon('自己的生活','她会按兴趣去看、去做点什么，遇到趣事来找你分享，并等你回应；每天主动发言的次数可以在这里调。'))
        proactive_page.add(ComingSoon('允许她自己退出','情绪和情境合适时，她会告别并真的关掉自己；关掉这个开关后她只会表示不想说话。'))
        proactive_page.finish()
        self.refresh_reminders()

        # ---- 模型与 Key（正式的模型解析器才有配置可改；测试里的固定客户端没有） -----------------------
        self.model_page=None
        models_page=Page('模型与 Key','为聊天、识图、搜索、办事、语音合成分别选服务和模型；可以给某个角色单独指定。这一页的改动选了就生效。')
        if hasattr(controller.router,'store'):
            from aicompanion.ui.model_page import ModelPage
            self.model_page=ModelPage(controller.router,[(c.id,c.name) for c in self.database.characters()],cid,self,speech_output=speech_output)
            models_page.add(self.model_page)
        else:
            models_page.add(QLabel('当前是测试用的固定模型，没有可修改的配置。'))
        models_page.finish()

        # ---- 其余页面（各自原有的控件，按用途归组） -----------------------------------------------
        from aicompanion.ui.agent_pages import AgentPage, AuditPage, UsagePage
        from aicompanion.ui.bridge_page import BridgePage
        from aicompanion.ui.library_page import LibraryPage
        from aicompanion.ui.relationship_page import RelationshipPage   # T15
        from aicompanion.ui.community_page import CommunityPage           # T18
        from aicompanion.ui.storage_page import StoragePage
        self.agent_page=AgentPage(self.database); self.audit_page=AuditPage(self.database)
        agent_page=Page('办事','让她动手帮你做电脑上的事：读写文件、整理资料、写博客。改动文件前会先问你。')
        agent_settings=QWidget(); agent_box=QVBoxLayout(agent_settings); agent_box.setContentsMargins(0,6,0,0)
        agent_box.addWidget(self.agent_page)
        agent_box.addWidget(ComingSoon('把文件交给她','在聊天面板附上或拖入文件，让她读、整理或改写；文件先复制到工作区，原件不动。'))
        agent_box.addWidget(ComingSoon('跨天的任务','任务有进度和检查点，重启后从断点继续；你也可以在 QQ 私聊里交办，审批发到你那里确认。'))
        agent_box.addStretch(1)
        agent_page.add(tabs((scrolled(agent_settings),'设置'),(self.audit_page,'操作日志')),1)
        self.library_page=LibraryPage(self.database,cid,self,controller=controller)
        library_page=Page('表情图库','她能发的表情包。自己的图放进目录就能用，含义由识图模型自动识别。'); library_page.add(self.library_page)
        self.relationship_page=RelationshipPage(self.database,cid,self)
        relation_page=Page('关系与心情','她现在的心情，以及她和每个人的关系（桌面主人、QQ 上的朋友）。')
        from aicompanion.ui.mood_card import MoodCard
        self.mood_card=MoodCard(self.database,cid,self)
        relation_page.add(Section('她现在的心情','心情会延续一段时间、慢慢回落：被冷落会委屈、被欺负会生气、被哄会消气。'
                                  '容不容易生气、消气快慢在“角色与形象 → 人格 → 情绪倾向”里调。心情不改好感，也不影响办事。',self.mood_card))
        relation_page.add(self.relationship_page)
        relation_page.add(ComingSoon('陪伴日期','记得你们认识了多少天、哪些日子值得纪念；到了那天她会自己提起。'))
        relation_page.finish()
        self.bridge_page=BridgePage(self.database); self.community_page=CommunityPage(self.database,cid,self)
        platforms_page=Page('QQ 与社区','让她在 QQ 上和你、和群友聊天（通过 AstrBot + NapCat 插件），以及她从群里学到的梗。')
        platforms_page.add(tabs((self.bridge_page,'QQ 聊天'),(scrolled(self.community_page),'社区与黑话')),1)
        self.storage_page=StoragePage(self.database,settings)
        storage_page=Page('存储位置','聊天记录、记忆和表情图库放在哪里。'); storage_page.add(self.storage_page); storage_page.finish()
        self.usage_page=UsagePage(self.database)
        rows=self.database.usage_summary()
        other=QLabel('本月其他用量：'+('；'.join(f"{r['kind']} {r['amount']}" for r in rows) or '暂无')+'\nKey 在“模型与 Key”页加密保存（Windows DPAPI），不以明文写入数据库。')
        other.setWordWrap(True); other.setObjectName('Hint')
        usage_page=Page('用量','每天的模型和语音用量，以及额度上限。'); usage_page.add(self.usage_page); usage_page.add(other); usage_page.finish()

        # ---- 概览（最后建：要读上面各页的状态） ------------------------------------------------------
        overview=self._overview_page(Page, Section, ComingSoon)

        nav=self.nav
        nav.add_page('overview','概览',overview,'还差哪几步、常用操作在哪里')
        nav.add_group('她')
        nav.add_page('persona','角色与形象',persona); nav.add_page('relationship','关系与心情',relation_page)
        nav.add_page('memory','记忆与心事',memory_page); nav.add_page('proactive','主动与提醒',proactive_page)
        nav.add_group('能力')
        nav.add_page('models','模型与 Key',models_page); nav.add_page('voice','声音与通话',voice_page)
        nav.add_page('agent','办事',agent_page); nav.add_page('library','表情图库',library_page)
        nav.add_group('连接')
        nav.add_page('platforms','QQ 与社区',platforms_page)
        nav.add_group('系统')
        nav.add_page('storage','存储位置',storage_page); nav.add_page('usage','用量',usage_page)

        footer=QWidget(); footer.setObjectName('Footer'); bar=QHBoxLayout(footer); bar.setContentsMargins(18,10,18,10)
        tip=QLabel('“模型与 Key”“表情图库”里的改动立即生效；其他页改完点“保存”。'); tip.setObjectName('Hint'); bar.addWidget(tip,1)
        cancel=QPushButton('取消'); cancel.clicked.connect(self.reject); bar.addWidget(cancel)
        save=QPushButton('保存'); save.setObjectName('Primary'); save.setDefault(True); save.clicked.connect(self.save); bar.addWidget(save)
        layout.addWidget(footer)
        self.refresh()
        self.open_page(page or 'overview')

    def open_page(self,key):
        """跳到某一页（概览的“去设置”、聊天面板的引导条都用它）。"""
        return self.nav.show_page(key)

    def _overview_page(self,Page,Section,ComingSoon):
        """概览：几项关键设置的状态 + 一键跳转，再列出常用操作和即将推出的功能。"""
        cid=self.controller.character.id; router=self.controller.router
        page=Page(f'你好，这里是{self.controller.character.name}的设置','先把下面几项看一遍；带 ✓ 的已经可以用了。')
        steps=Section('准备情况','')
        from PySide6.QtWidgets import QGridLayout
        grid=QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(10); grid.setColumnStretch(1,1)
        def row(title,ok,text,key,action='去设置'):
            n=grid.rowCount(); name=QLabel(title); name.setObjectName('CardTitle')
            state=QLabel(('✓ ' if ok else '○ ')+text); state.setObjectName('Good' if ok else 'Warn'); state.setWordWrap(True)
            go=QPushButton(action); go.setObjectName('Link'); go.clicked.connect(lambda _=False,k=key:self.open_page(k))
            top=Qt.AlignmentFlag.AlignTop
            grid.addWidget(name,n,0,top); grid.addWidget(state,n,1,top); grid.addWidget(go,n,2,top)
        try: chat_ok=bool(router.configured('chat',cid))
        except Exception: chat_ok=False
        row('聊天模型',chat_ok,('现在用：'+router.label(cid)) if chat_ok else '还没有配置，现在是离线模拟回复。先添加一个账户并选聊天模型','models')
        try: target=router.agent_target(cid); problem='' if target else router.agent_problem(cid)
        except Exception: target,problem=None,''
        if not target and (not problem or problem.startswith('还没有配置')): problem='还没有选办事模型（可选，在“模型与 Key”页的“办事”里选）'
        row('办事',target is not None,('办事模型：'+target.label) if target else problem,'models')
        providers={'system':'本地朗读','cloud':'智谱语音','qwen':'千问动漫声线','breeze':'Breeze Blue'}
        tts=self.database.get_setting('tts_provider',self.settings.tts_provider)
        row('声音',True,f'朗读方式：{providers.get(tts,tts)}；在聊天面板点“朗读”打开','voice')
        from aicompanion.bridge.service import setting as bridge_setting
        bridge=bool(bridge_setting(self.database,'bridge_enabled'))
        row('QQ',bridge,'QQ 桥接已打开' if bridge else '没有接 QQ（可选）','platforms')
        meme=str(self.database.get_setting('qq_meme_root','') or '')
        row('表情图库',bool(meme),f'图库目录：{meme}' if meme else '还没选表情包目录（可选）','library')
        steps.add(grid); page.add(steps)
        tips=QLabel('· 在聊天面板直接打字和她聊天；点输入框左边的“办事”，这句话就交给她动手做。\n'
                    '· 点“通话”后按住右 Ctrl 说话，松开发送；她说话时你开口也能打断她。\n'
                    '· 右键桌面上的她：打开聊天、切换角色、让她办事、退出。\n'
                    '· 单击托盘图标召回她，双击打开聊天面板。')
        tips.setWordWrap(True); page.add(Section('常用操作','',tips))
        page.add(ComingSoon('接下来会有','人格包与版本 · 持续的心情与小脾气 · 她自己的生活和趣事分享 · 想得起更久以前的事 · '
                                  '圈子话题与约定 · 把文件交给她办事 · 跨天任务与 QQ 交办 · 自己告别退出。具体见项目计划书 v4。'))
        return page.finish()

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
        elif self.provider.currentData()=='breeze':
            # 音色在“模型与 Key”页的“语音合成”里按角色设置，这里只显示当前生效的
            target=self.controller.router.tts_target(self.controller.character.id)
            current=target.cache_id if target else ''
            self.voice.addItem(target.label if target else '未配置：请到“模型与 Key”页设置音色',current)
        else:
            current=self.controller.character.voice or self.database.get_setting('tts_voice','tongtong')
            self.voice.addItem('彤彤','tongtong')
        index=self.voice.findData(current)
        if index>=0: self.voice.setCurrentIndex(index)
        else: self.voice.setEditText(current)
        self.voice.setEnabled(self.provider.currentData() not in ('system','breeze'))

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
            '' if self.provider.currentData() in ('qwen','breeze') else self.selected_voice())
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
        if self.model_page is not None and not self.model_page.save(): return
        if not self.name.text().strip() or not self._validate_model_input(): return
        if not self.persona_editor.save(): return
        # 设定和说话风格以当前人格为准（人格编辑器保存时已写回角色表）；这里只改名字、形象和声音
        current=next((c for c in self.database.characters() if c.id==self.controller.character.id),self.controller.character)
        character=replace(current,name=self.name.text().strip(),model_path=self.model.text().strip(),
                          voice=self.controller.character.voice if self.provider.currentData() in ('qwen','breeze') else self.selected_voice())
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
        try: self.storage_page.save()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '存储位置没有保存', str(error)); return
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
