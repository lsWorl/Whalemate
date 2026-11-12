"""v2 桌宠自检：常驻显示、原生立绘、透明与穿透遮罩、状态切换、气泡、交互与缩放。

只用临时数据库和离线模型；不打开麦克风、不联网。每个姿势截图保存到 output，便于人工复核。
"""
import json
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from aicompanion.ui.sprite_view import PlaceholderAvatar, SpriteAvatar
from aicompanion.ui.animation_view import AnimationAvatar
try:
    from aicompanion.body.rig_view import RigAvatar
except ImportError:  # pragma: no cover
    RigAvatar = SpriteAvatar
NATIVE = (SpriteAvatar, RigAvatar, AnimationAvatar, PlaceholderAvatar)


def schedule_pet_smoke_check(window, database, output):
    output.mkdir(parents=True, exist_ok=True)
    state = {'errors': [], 'poses_seen': []}
    # 后面会主动触发一次调度来验心事，隔离库里的兴趣搜索要关闭，离线自检不能向外发请求。
    database.set_setting('life_enabled', False)
    pet = window.pet
    pet.input_locked = True   # 自检不受手动拖动、点菜单影响
    # 记录台词标注、用户气泡、活动图标的调用，验证 P2 链路
    state['cues'] = []; state['user_bubbles'] = 0; state['activities'] = []
    original_perform, original_user, original_activity = pet.perform, pet.show_user, pet.show_activity
    pet.perform = lambda cue: (state['cues'].append(f'{cue.kind}:{cue.name}'), original_perform(cue))[1]
    pet.show_user = lambda text: (state.update(user_bubbles=state['user_bubbles']+1), original_user(text))[1]
    pet.show_activity = lambda kind='thinking': (state['activities'].append(kind), original_activity(kind))[1]
    rig = isinstance(pet.view, RigAvatar)
    animation = isinstance(pet.view, AnimationAvatar)
    initial_view = pet.view
    placeholder = isinstance(initial_view, PlaceholderAvatar)
    pack = window.registry.pack(window.controller.character.id)
    if pack and pack.renderer_kind == 'spritesheet':
        installed = (pack.root/pack.renderer['animation']).is_file()
        state['aboutme_installed'] = installed
        if installed:
            state['expected_renderer'] = 'AnimationAvatar'
            state['expected_renderer_shown'] = animation and pet.view.using_aboutme
        else:
            # 出厂自绘形象在没有 Aboutme 的新安装中也应正常显示。
            expected = RigAvatar if database.get_setting('rig_enabled', True) else SpriteAvatar
            state['expected_renderer'] = expected.__name__
            state['expected_renderer_shown'] = isinstance(pet.view, expected)
    else:
        state['expected_renderer_shown'] = not placeholder
    animation_frames=[]
    if animation:
        pet.view.frameAdvanced.connect(animation_frames.append)
        state['pose_transitions_started']=[];state['pose_transitions_finished']=[]
        pet.view.transitionStarted.connect(lambda a,b:state['pose_transitions_started'].append([a,b]))
        pet.view.transitionFinished.connect(lambda a,b:state['pose_transitions_finished'].append([a,b]))
    state['renderer'] = type(pet.view).__name__
    if rig:
        # 骨骼渲染要等 WebEngine 页面和贴图加载；时间线整体后移。
        pet.view.bridge.loaded.connect(lambda value: state.update(rig_loaded=value))
    offset = 2500 if rig else 0
    def later(ms, fn): QTimer.singleShot(ms+offset, fn)
    # 计划书 v4 功能八：新库默认开着朗读（智能朗读）。记下后关掉，后面的检查不出声
    state['speech_default_on'] = window.speak_toggle.isChecked() and window._speech_style() == 'smart'
    window.speech.enabled = False; window.speak_toggle.setChecked(False)
    window.hide()  # v2：聊天面板平时隐藏，角色独立常驻

    def record_pose(pose):
        if not state['poses_seen'] or state['poses_seen'][-1] != pose: state['poses_seen'].append(pose)
    if isinstance(pet.view, NATIVE): pet.view.poseShown.connect(record_pose)

    def startup():
        state['pet_visible'] = pet.isVisible()
        state['chat_hidden'] = not window.isVisible()
        state['native_view'] = isinstance(pet.view, NATIVE)
        state['webengine_not_created'] = window.web_avatar is None
        state['always_on_top'] = bool(pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        state['translucent'] = pet.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        image = pet.grab().toImage()
        state['corner_alpha'] = image.pixelColor(1, 1).alpha()
        state['center_alpha'] = image.pixelColor(image.width()//2, int(image.height()*.55)).alpha()
        if animation:
            # 动画透明区域随帧改变，不裁剪窗口；输入穿透使用当前帧 alpha。
            state['mask_active']=pet.mask().isEmpty()
            state['mask_excludes_corner']=not pet.view.hit(pet.view.rect().topLeft())
            state['animation_body_hit']=pet.view.hit(pet.view.rect().center())
            state['animation_background_decode']=pet.view.decoder.thread() is not QApplication.instance().thread()
            state['animation_frame_count']=len(set(animation_frames))
        elif rig:
            # 骨骼形象不裁剪窗口；穿透靠光标探测。直接问网页：角落不命中、身体命中。
            state['mask_active'] = pet.mask().isEmpty()
            pet.view.page().runJavaScript('JSON.stringify(rigApi.gpu())', lambda raw: state.update(webgl=raw))
            pet.view.page().runJavaScript('JSON.stringify(rigApi.diagnostics())', lambda raw: state.update(rig_diagnostics=raw))
            w, h = pet.view.width(), pet.view.height()
            pet.view.page().runJavaScript(f'JSON.stringify([rigApi.probe(2,2).hit, rigApi.probe({w/2},{h*0.75}).hit])',
                lambda raw: state.update(mask_excludes_corner=raw == '[false,true]', probe_raw=raw))
        else:
            mask = pet.mask().boundingRect()
            state['mask_active'] = not pet.mask().isEmpty() and mask.width() <= pet.width() and mask.height() <= pet.height()
            state['mask_excludes_corner'] = not pet.mask().contains(pet.rect().topLeft())
        state['size'] = [pet.width(), pet.height()]
        state['model_label'] = window.model_label
        state['character'] = window.controller.character.id
        image.save(str(output/'pet-idle.png'))
    later(900, startup)

    later(1300, lambda: window.submit('今天有点累。'))
    later(1500, lambda: window.submit('你好呀，你真可爱！'))

    def poke():
        pet.dispatch('poke')
        state['bubble_after_poke'] = pet.bubble.isVisible() and bool(pet.bubble.text)
        state['bubble_text'] = pet.bubble.text
        def bubble_check():
            # 文字必须完整放进气泡：标签所需高度不超过实际高度。
            label = pet.bubble.label
            state['bubble_text_fits'] = label.heightForWidth(label.width()) <= label.height() and label.text() == pet.bubble.full_text
            pet.bubble.grab().save(str(output/'bubble.png'))
        QTimer.singleShot(900, bubble_check)
    later(7000, poke)
    later(7300, lambda: state.update(pose_after_poke=pet.machine.shown))

    def grab():
        pet.dispatch('grab', active=True)
        QTimer.singleShot(200, lambda: state.update(pose_grabbed=pet.view.pose))
        QTimer.singleShot(250, lambda: pet.grab().save(str(output/'pet-grabbed.png')))
        QTimer.singleShot(700, lambda: pet.dispatch('grab', active=False))
    later(8000, grab)

    poses = ['celebrate', 'thinking', 'working', 'wait', 'sleepy', 'error', 'talking', 'patted']
    def force(index):
        if index >= len(poses): return
        pose = poses[index]
        pet.machine.snapshot = type(pet.machine.snapshot)()  # 清空其他状态，单独展示该姿势
        pet.dispatch('force_pose', pose=pose, seconds=1.2)
        QTimer.singleShot(650, lambda: (state.setdefault('forced', {}).__setitem__(pose, pet.view.pose), pet.grab().save(str(output/f'pet-{pose}.png'))))
        QTimer.singleShot(900, lambda: force(index+1))
    later(9500, lambda: force(0))

    def scale_and_hide():
        bottom = pet.geometry().bottom()
        pet.set_scale(1.2)
        state['scale_keeps_feet'] = abs(pet.geometry().bottom()-bottom) <= 2
        state['scaled_size'] = [pet.width(), pet.height()]
        pet.set_scale(1.0)
        pet._hide_by_user(); state['hidden_by_user'] = not pet.isVisible()
        pet.summon(); state['summoned'] = pet.isVisible()
        pet.save_geometry(); state['geometry_saved'] = bool(database.get_setting('desktop_pet_geometry'))
    later(17500, scale_and_hide)
    def appearance():
        if rig and state.get('aboutme_installed') is False:
            # 同一份无 Aboutme 的发行包，关闭骨骼后仍应显示自绘整图。
            database.set_setting('rig_enabled', False)
            window._apply_avatar()
            state['fallback_sprite_shown'] = isinstance(pet.view, SpriteAvatar)
            pet.grab().save(str(output/'pet-fallback-sprites.png'))
            database.set_setting('rig_enabled', True)
            window._apply_avatar()
            state['fallback_rig_restored'] = isinstance(pet.view, RigAvatar)
        if not animation: return
        # 历史版本的关闭动画开关不能让已经安装的鲸鲸再次显示旧美术。
        cid = window.controller.character.id
        database.set_setting(f'pet_animation_enabled:{cid}', False)
        window._apply_avatar()
        state['legacy_setting_keeps_aboutme'] = isinstance(pet.view, AnimationAvatar) and pet.view.using_aboutme
        database.set_setting(f'pet_animation_enabled:{cid}', True)
        window._apply_avatar()
    later(18000,appearance)

    def switch_role():
        # 切到一个用户自建的角色（没有形象包），再切回鲸娘
        from aicompanion.domain.character import CharacterProfile
        database.add_character(CharacterProfile('自检角色', '用于自检的临时角色', '简短回答。', 'smoke-other'))
        window._switch_to('smoke-other')
        state['switched_to_other'] = window.controller.character.id == 'smoke-other'
        window._switch_to('deepseek-whale')
        if pet.view is not initial_view:
            pet.view.poseShown.connect(record_pose)
        state['switched_back'] = type(pet.view) is type(initial_view) and window.controller.character.id == 'deepseek-whale'
    later(18500, switch_role)
    def throw():
        # 从半空甩出去：应当经历下落并精确落在屏幕底边
        state['throw_start_y'] = pet.y(); ground = pet._ground_y(); state['ground_y'] = ground
        pet.move(pet.x(), ground-420); pet.start_fall(900, -700)
        QTimer.singleShot(2200, lambda: state.update(landed_on_ground=abs(pet.y()-pet._ground_y()) <= 1 and not pet._fall_timer.isActive()))
    later(19500, throw)

    def brain():
        # P3：互动事件 → 记忆；心事被主动说出并标记为已说过
        cid = window.controller.character.id
        for _ in range(2): database.add_inbox(cid, 'pat')
        window._organize_memory()
        state['interaction_memory'] = any('被你摸了头' in r['content'] for r in database.memory_card(cid))
        database.upsert_memory('用户在准备下周的考试', cid, 'smoke:exam', 'event', 3)
        mid = next(r['id'] for r in database.memory_card(cid) if r['fact_key'] == 'smoke:exam')
        from datetime import datetime
        pid = database.add_purpose(cid, 'understand', '想知道考试准备得怎么样了', '考试准备得怎么样啦？', [mid], datetime.now().isoformat(timespec='seconds'))
        state['purpose_id'] = pid
        # 统一主动调度：直接让调度器给出这件心事（时间、上限这些门槛在 tests/test_scheduler.py 里测），这里看桌宠能不能接住并说出来
        from aicompanion.brain.scheduler import Outreach
        row = next(r for r in database.purposes(cid, 'pending') if r['id'] == pid)
        real_next = window.outreach_scheduler.next
        window.outreach_scheduler.next = lambda *a, **k: Outreach('purpose', f'purpose:{pid}', window.purposes.intent(row), {'purpose': row})
        window._proactive(); window.outreach_scheduler.next = real_next
        state['purpose_started'] = bool(window.outreaches) and any(r['id'] == pid and r['status'] == 'delivering' for r in database.purposes(cid))
    later(20500, brain)

    def voice():
        # P4：Chromium 收音播放网页能加载并接上通道（不真的开麦克风）；切换播放端不报错
        web = window._web_microphone()
        web.bridge.page_ready.connect(lambda: state.update(voice_page_ready=True))
        web.ensure_page()
        window.database.set_setting('echo_backend', 'web'); window._configure_voice()
        state['voice_sink_web'] = window.speech.sink is web.sink
        window.database.set_setting('echo_backend', 'qt'); window._configure_voice()
        state['voice_sink_restored'] = window.speech.sink is None and window.microphone is window.qt_microphone
        state['voice_hint'] = window._call_hint()
    later(21000, voice)

    def storage_locations():
        from aicompanion.ui.storage_page import StoragePage
        page = StoragePage(database, window.settings)
        state['storage_locations_ready'] = (page.memory.text() == str(database.database_path.resolve().parent)
                                             and bool(page.memes.text()))
        page.deleteLater()
    later(21500, storage_locations)

    def qq_media():
        # 设置页真实挂载媒体选项；只操作自检数据库，不启动 QQ 或调用模型。
        from aicompanion.ui.bridge_page import BridgePage
        page = BridgePage(database); page.timer.stop()
        state['qq_media_settings_ready'] = (page.vision_enabled.isChecked() and page.memes_enabled.isChecked()
                                            and page.chat_card_enabled.isChecked())
        page.deleteLater()
    later(21500, qq_media)

    def ui_refresh():
        # 界面翻新（2026-11-11）：应用图标、聊天面板的办事开关与附件占位、分组设置窗口能逐页打开
        from aicompanion.ui.settings_window import SettingsWindow
        state['app_icon_ready'] = not window.windowIcon().isNull() and not window.tray.icon().isNull()
        state['panel_controls_ready'] = (window.agent_toggle.isCheckable() and not window.attach_button.isEnabled()
                                         and window.speak_toggle.isCheckable())
        from aicompanion.ui.onboarding import Onboarding
        menu = window.pet.build_menu()
        sections = [a.text() for a in menu.actions() if not a.isEnabled() and a.text()]
        state['menu_groups_ready'] = sections == ['和她说话', '让她帮忙', '她的样子']
        menu.deleteLater()
        wizard = Onboarding(window)
        visited = []
        for _ in range(5):
            visited.append(wizard.pages.currentIndex())
            wizard.next.click()
        state['wizard_complete'] = visited == list(range(5)) and database.get_setting('onboarding_done', False)
        wizard.deleteLater()
        dialog = SettingsWindow(window.controller, window.settings, window, page='models')
        try:
            state['settings_pages_ready'] = (dialog.nav.current() == 'models'
                                             and all(dialog.open_page(key) for key in SettingsWindow.PAGES))
            card = dialog.dates_card
            card.kind.setCurrentIndex(card.kind.findData('custom'))
            card.title.setText('自检纪念日')
            card.add()
            timing = card.table.cellWidget(0, 4)
            timing.setCurrentIndex(timing.findData('eve'))
            state['date_reminder_editable'] = (timing.count() == 4 and card.reminder.currentData() == 'day'
                                                and card.dates.user_dates(card.cid)[0]['reminder'] == 'eve')
            card.table.selectRow(0); card.remove()
            # 计划书 v4 功能二：人格编辑器能载入当前人格
            state['persona_ready'] = bool(dialog.persona_editor.current_id) and bool(dialog.persona_editor.description.toPlainText())
            # 计划书 v4 功能三：心情卡能读出当前心情
            state['mood_ready'] = bool(dialog.mood_card.summary.text())
            state['self_exit_default_on'] = dialog.self_exit.isChecked()
            dialog.self_exit.click()
            state['self_exit_switch_saved'] = database.get_setting('self_exit_enabled') is False
            if hasattr(dialog.library_page, 'analyzer') and dialog.library_page.analyzer is not None:
                dialog.library_page.analyzer.wait(5000)
        finally:
            dialog.reject(); dialog.deleteLater()
    later(21600, ui_refresh)

    def expression_start():
        import re
        from uuid import uuid4
        original=window.controller.router
        # 该计数器只测一次表达回复。临时库停用回复完成后独立触发的记忆和关系分析，
        # 否则它们也会使用下面的合成客户端，给同一个计数器再加一次。
        database.set_setting('memory_analysis', False)
        database.set_setting('relationship_learning', False)
        class ExpressionModel:
            calls=0
            def generate_reply_stream(self,history,prompt,cancellation):
                self.calls+=1
                handle=re.search(r'^(s\d+)｜',prompt,re.M).group(1)   # 本轮候选短编号
                yield '<sticker id="'+handle+'"/>'
                yield '【开心】好耶，表达自检完成！<挥手>'
                yield '<lea'
                yield 've reason="想休息一下"/>'
        model=ExpressionModel();window.controller.use_client(model)
        generation=uuid4().hex;window.coordinator.active=generation
        window._start(generation,query='合成的庆祝消息')
        # Codex 审计（ee541d0）：不再固定等 400ms。轮询到这一轮结束、表达决策到达最终阶段（或总超时）再检查，
        # 慢机器上不会因为还在 E_pending 误判；超时也照常继续后面的检查并写明停在哪个阶段，不吞掉第一次失败。
        started=time.monotonic(); final=('OK_displayed','OK_accepted','F_display_failed','F_send_unknown','E_not_delivered',
                                         'A_no_candidates','B_model_chose_text','C_marker_unparsed','D_asset_invalid')
        def latest():
            mine=[t for t in window.controller.expression.traces(10) if t.get('turn')==generation[-12:]]
            return mine[-1] if mine else None
        def poll():
            trace=latest()
            if model.calls and not window.workers and trace and trace.get('stage') in final:return check(trace,False)
            if time.monotonic()-started>EXPRESSION_TIMEOUT:return check(trace,True)
            QTimer.singleShot(100,poll)
        def check(trace,timed_out):
            try:
                state['expression_wait_ms']=round((time.monotonic()-started)*1000)
                state['expression_trace_stage']=trace.get('stage') if trace else None
                if timed_out:state['errors'].append('表达自检超时：停在 '+str(state['expression_trace_stage']))
                state['expression_flow']=bool(window.controller.expression.history(generation,window.controller.character.id))
                state['expression_single_request']=model.calls==1
                state['expression_clean_text']='sticker' not in window.texts.get(generation,'')
                state['self_exit_disabled_reply'] = (window.texts[generation] == '好耶，表达自检完成！'
                                                      and window.departure is None and not window.closing)
                state['expression_trace_ready']=state['expression_trace_stage']=='OK_displayed'
                from aicompanion.ui.library_page import LibraryPage
                page=LibraryPage(database,window.controller.character.id)
                state['expression_library_ready']=page.table.rowCount()>=6
                # 扫描可能在线程启动后立刻完成。由 UI 循环核对页面已处理的结果，
                # 避免在 scan() 返回后才接 finished 而错过信号。
                page.scan()
                deadline = time.monotonic() + 4
                window._load_history(); window.show()  # 可见窗口完成布局后再截图，避免离屏抓图使用旧几何。
            except Exception as error:
                state['errors'].append('表达自检：'+repr(error)); window.controller.router=original
                QTimer.singleShot(300,after_expression); return
            def restored():
                try:
                    from aicompanion.ui.expression_image import ExpressionImage
                    state['expression_history_ready']=bool(window.chat_list.findChildren(ExpressionImage))
                    window.grab().save(str(output/'expression-panel.png')); window.hide()
                finally:QTimer.singleShot(300,after_expression)
            def check_scan(round_number):
                if page.worker is not None and time.monotonic() < deadline:
                    QTimer.singleShot(25, lambda: check_scan(round_number))
                    return
                done = page.worker is None and '处理完成' in page.status.text()
                if done and round_number == 1:
                    page.scan()
                    QTimer.singleShot(25, lambda: check_scan(2))
                    return
                state['expression_library_background_ready'] = done and round_number == 2
                if page.worker is None:
                    page.deleteLater()
                QTimer.singleShot(200, restored)
            QTimer.singleShot(25, lambda: check_scan(1))
            window.controller.router=original
        QTimer.singleShot(100,poll)
    later(24200,expression_start)
    EXPRESSION_TIMEOUT = 8.0   # 秒；正常几百毫秒就到最终阶段

    def agent():
        # P5/P6：干活交给 DeepSeek Harness（需要联网和真实模型，自检只检查是否已安装、会话对象是否就绪）
        from aicompanion.agent.dsh_backend import problem
        state['dsh_problem'] = problem()
        state['agent_session_ready'] = window.agent is not None and not window.agent.busy
        # 她的工具：MCP 服务能应答；.env 读不到；工作区外写会先问（自检里自动拒绝）
        from pathlib import Path as _Path
        from aicompanion.ui.chat_worker import TaskWorker
        from aicompanion.agent.mcp_server import tool_client
        import asyncio
        session = window.agent
        async def list_tools():
            async with tool_client(session.server, mode='legacy') as client:
                return len((await client.list_tools()).tools)
        worker = TaskWorker(lambda token: asyncio.run(list_tools()), window)
        window.tasks.add(worker)
        worker.succeeded.connect(lambda count: state.update(tools_listed=count))
        worker.failed.connect(lambda error: state['errors'].append('MCP 自检：'+error))
        worker.finished.connect(lambda: (window.tasks.discard(worker), worker.deleteLater()))
        worker.start()
        _, blocked = session.box.call('read_file', {'path': str(_Path(__file__).resolve().parents[3]/'.env')})
        state['env_blocked'] = blocked
        asked = []; session.auto_approve = lambda kind, title, detail: (asked.append(kind), 'no')[1]
        from aicompanion.agent.tools_server import Task
        import tempfile
        inside = _Path(tempfile.mkdtemp()); outside = _Path(tempfile.mkdtemp())/'x.txt'
        session.box.task = Task(run_id=0, allowed=[inside])
        _, inside_error = session.box.call('write_file', {'path': str(inside/'ok.txt'), 'content': 'hi'})
        _, outside_error = session.box.call('write_file', {'path': str(outside), 'content': 'hi'})
        state['tools_gate'] = (not inside_error) and outside_error and asked == ['write'] and not outside.exists()
        session.auto_approve = None
        # 自检只验证清理结果的接口，不清理用户真实的回收区或 DSH 会话。
        from aicompanion.agent.housekeeping import Report
        state['housekeep_ok'] = isinstance(Report().text(), str)
    later(21800, agent)

    def offer_and_quota():
        # "要我动手吗"确认气泡能弹出；额度用完时陪伴聊天不再调用模型
        database.set_setting('agent_auto', False)   # 这里只看"要我动手吗"气泡；自动开始的路径在上面的离线干活流程里
        window._offer_task('整理下载文件夹')
        state['task_offer_shown'] = window.pet.confirm is not None and window.pet.confirm.isVisible()
        window.pet.close_confirm()
        database.set_setting('quota_daily_total', 0.01)
        database.add_token_usage('companion', 'smoke', 1, 1, cost=0.02)
        before = len(window.workers)
        window.coordinator.submit('额度用完还能聊吗'); window.coordinator.last_at -= 5; window._pump()
        state['quota_blocks_model'] = len(window.workers) == before
        database.set_setting('quota_daily_total', 0)

    def panel_shot():
        # 自检只用纯色背景，不读取用户的私人立绘。
        database.set_setting('panel_background', ''); window._apply_backdrop()
        window.show(); window.resize(600, 760)
        QTimer.singleShot(400, lambda: (window.grab().save(str(output/'panel.png')), state.update(panel_shot=True, panel_bubbles=window.chat_list.column.count()-1), window.hide()))
    later(23500, panel_shot)

    def after_expression():
        # 表达检查结束后再做额度检查和退出（原来固定在 25.0s / 26.5s，表达检查变成"等到完成"后改成串联）
        if state.get('after_expression_ran'): return
        state['after_expression_ran'] = True
        def finish_checks():
            try: offer_and_quota()
            finally: QTimer.singleShot(1500, window.request_exit)
        if not animation:
            finish_checks();return
        def check_walking():
            view=pet.view;view.perform('expression','neutral')
            pet.machine.snapshot=type(pet.machine.snapshot)();pet.machine.shown='idle';pet._apply_state()
            start=(pet.x(),pet.y());reference=not view.supports_vertical_walk
            routes=[(-60,0),(60,0)] if reference else [(-60,-40),(0,30),(50,0)]
            state['walk_routes']=[];state['walk_frame_indices']=[]
            def walk_next(index):
                if index==len(routes):
                    if reference:
                        state['walk_mode']='aboutme_left_right' if view.using_aboutme else 'reference_left_right'
                        state['walk_flow']=(len(state['walk_routes'])==2 and [r['gait'] for r in state['walk_routes']]==['walking_left','walking_right']
                            and all(r['moved_x'] and not r['moved_y'] and r['target_reached'] for r in state['walk_routes'])
                            and len(set(state['walk_frame_indices']))>=3)
                    else:
                        state['walk_mode']='legacy_2d'
                        state['walk_flow']=(len(state['walk_routes'])==3 and state['walk_routes'][0]['gait']=='walking_back'
                            and all(r['moved_x'] or r['moved_y'] for r in state['walk_routes'])
                            and state['walk_routes'][0]['moved_x'] and state['walk_routes'][0]['moved_y']
                            and len(set(state['walk_frame_indices']))>=3)
                    finish_checks();return
                dx,dy=routes[index];before=(pet.x(),pet.y());pet.walk_to(before[0]+dx,before[1]+dy)
                target=pet._walk_target;deadline=time.monotonic()+(6. if reference else 4.);gait=[None]
                def wait_walk():
                    if view.walking_ready:
                        gait[0]=view._loaded;state['walk_frame_indices'].append(view._index)
                    if pet._walk_target is None:
                        state['walk_routes'].append({'gait':gait[0],
                            'moved_x':pet.x()!=before[0],'moved_y':pet.y()!=before[1],
                            'target_reached':target is not None and (pet.x(),pet.y())==tuple(round(v) for v in target)})
                        pet.grab().save(str(output/f'pet-walk-{index}.png'))
                        if not state['walk_routes'][-1]['target_reached']:state['errors'].append('行走目标未到达')
                        QTimer.singleShot(40,lambda:walk_next(index+1))
                    elif time.monotonic()>=deadline:
                        state['errors'].append('二维行走超时');pet.stop_walking();finish_checks()
                    else:QTimer.singleShot(25,wait_walk)
                QTimer.singleShot(25,wait_walk)
            walk_next(0)

        def check_reference(index=0):
            keys=['walking_left','walking_right','click_wave','curtsy','curious','shy_peek','happy_hops','self_hug','pout_stomp','giggle']
            aboutme=pet.view.using_aboutme
            if aboutme:keys=list(pet.view.pack.actions)
            if not (pet.view.using_reference or aboutme):state['reference_flow']=True;check_walking();return
            if index==0:state['reference_actions']=[]
            if index==len(keys):state['reference_flow']=state['reference_actions']==keys;check_walking();return
            key=keys[index];pet.view.face(-1 if key=='walking_left' else 1);pet.view.play_action(key)
            deadline=time.monotonic()+4.
            def ready():
                if pet.view._loaded==key and not pet.view.transitioning:
                    clip=pet.view.pack.actions[key]
                    if aboutme:
                        if clip.fps!=12 or clip.count not in (59,80,120,180):state['errors'].append('Aboutme 动作规格不符：'+key)
                    else:
                        expected=(240,24) if key.startswith('walking') else (120,12)
                        if (clip.count,clip.fps)!=expected:state['errors'].append('新动作规格不符：'+key)
                    state['reference_actions'].append(key);QTimer.singleShot(80,lambda:check_reference(index+1))
                elif time.monotonic()>deadline:state['errors'].append('新动作加载超时：'+key);finish_checks()
                else:QTimer.singleShot(25,ready)
            ready()

        def check_personality(index=0):
            keys=['smug','pout','shy','surprised','milk_tea','stretch']
            if index==0:state['personality_actions']=[]
            if index==len(keys):
                state['personality_flow']=state['personality_actions']==keys
                check_reference();return
            key=keys[index];pet.view.perform('motion' if key in ('milk_tea','stretch') else 'expression',key)
            deadline=time.monotonic()+3.
            def wait_action():
                if pet.view._loaded==pet.view.pack.poses.get(key,key) and not pet.view.transitioning:
                    state['personality_actions'].append(key)
                    pet.grab().save(str(output/f'pet-personality-{key}.png'))
                    QTimer.singleShot(150,lambda:check_personality(index+1))
                elif time.monotonic()>=deadline:
                    state['errors'].append('性格动作未按时显示：'+key);finish_checks()
                else:QTimer.singleShot(25,wait_action)
            QTimer.singleShot(25,wait_action)

        # 实际播放站→坐→托腮→待机，包含两段衔接；不只核对 pose 字符串。
        reference_before=pet.view.using_reference
        if reference_before:pet._animation_variant(False)
        targets=['idle','working','thinking','idle'];state['pose_transition_targets']=[]
        transition_start=len(state['pose_transitions_finished'])
        # 前面的聊天特意播放挥手；本段单独验证姿态，清掉上一段的短时表演。
        pet.stop_walking();pet._stop_fall();pet._life_timer.stop()
        pet.view._cue=None;pet.view._cue_until=0.;pet.view.set_drag(False)

        def next_target(index):
            if index>=len(targets):
                expected=[['idle','working'],['working','idle']]  # 思考与待机同组直接混合，避免多余的待机绕行。
                state['pose_transition_flow']=(state['pose_transition_targets']==targets if pet.view.using_aboutme
                    else all(pair in state['pose_transitions_finished'][transition_start:] for pair in expected))
                if reference_before:
                    pet._animation_variant(True);state['animation_version_rollback']=pet.view.using_reference
                else:state['animation_version_rollback']=True
                check_personality();return
            pose=targets[index]
            pet.machine.snapshot=type(pet.machine.snapshot)()
            pet.dispatch('force_pose',pose=pose,seconds=8.)
            deadline=time.monotonic()+3.
            def wait_pose():
                view=pet.view
                if isinstance(view,AnimationAvatar) and view._loaded==pose and not view.transitioning:
                    state['pose_transition_targets'].append(pose)
                    if pose=='working':pet.grab().save(str(output/'pet-transition-working.png'))
                    QTimer.singleShot(30,lambda:next_target(index+1))
                elif time.monotonic()>=deadline:
                    state['errors'].append('动作过渡未按时衔接：'+pose);finish_checks()
                else:QTimer.singleShot(25,wait_pose)
            QTimer.singleShot(25,wait_pose)
        settled_deadline=time.monotonic()+3.
        def wait_chat_done():
            if not window.workers and not window.tasks and not window.speech.workers:
                pet.view._cue=None;pet.view._cue_until=0.;pet.view.set_walking(0);next_target(0)
            elif time.monotonic()>=settled_deadline:
                state['errors'].append('聊天线程未释放，无法独立验收过渡');finish_checks()
            else:QTimer.singleShot(25,wait_chat_done)
        wait_chat_done()
    def safety_exit():
        # 兜底：表达检查本身出异常、串联没走到时，也要退出并留下报告，而不是一直挂着
        if not state.get('after_expression_ran'):
            state['errors'].append('表达自检没有走完，兜底退出'); after_expression()
    later(24200+int(EXPRESSION_TIMEOUT*1000)+6000, safety_exit)

    def finish():
        rows = database.recent_messages(10, 'deepseek-whale')
        state['roles'] = [row['role'] for row in rows]
        state['chat_flow'] = 'thinking' in state['poses_seen'] and 'talking' in state['poses_seen']
        state['fell'] = 'falling' in state['poses_seen']
        if state.get('purpose_id'):
            row = next((r for r in database.purposes(window.controller.character.id) if r['id'] == state['purpose_id']), None)
            state['purpose_delivered'] = bool(row) and row['status'] == 'delivered'
            with database.transaction() as c:
                state['outreach_logged'] = c.execute("SELECT 1 FROM outreach_log WHERE source='purpose' AND key=?", (f"purpose:{state['purpose_id']}",)).fetchone() is not None
        assistant = [row['content'] for row in rows if row['role'] == 'assistant']
        state['reply_saved_clean'] = bool(assistant) and all('【' not in c and '<挥手>' not in c for c in assistant)
        state['cues_performed'] = 'expression:happy' in state['cues'] and 'motion:wave' in state['cues']
        state['user_bubble_shown'] = state['user_bubbles'] >= 2
        state['thinking_icon'] = 'thinking' in state['activities']
        state['forced_ok'] = all(state.get('forced', {}).get(p) == p for p in poses)
        state['remaining_workers'] = len(window.workers)+len(window.tasks)+len(window.speech.workers)
        required = ['pet_visible', 'chat_hidden', 'expected_renderer_shown', 'native_view', 'webengine_not_created', 'always_on_top', 'translucent',
                    'mask_active', 'mask_excludes_corner', 'bubble_after_poke', 'scale_keeps_feet', 'hidden_by_user', 'summoned',
                    'geometry_saved', 'chat_flow', 'forced_ok', 'bubble_text_fits', 'fell', 'landed_on_ground', 'reply_saved_clean', 'cues_performed', 'user_bubble_shown', 'thinking_icon', 'interaction_memory', 'purpose_started', 'purpose_delivered', 'outreach_logged', 'voice_page_ready', 'voice_sink_web', 'voice_sink_restored', 'agent_session_ready', 'task_offer_shown', 'quota_blocks_model', 'switched_to_other', 'switched_back']
        required.extend(['env_blocked', 'tools_gate', 'housekeep_ok'])
        required.append('storage_locations_ready')
        required.extend(['self_exit_default_on', 'self_exit_switch_saved', 'self_exit_disabled_reply'])
        if animation:
            required.extend(['animation_body_hit','animation_background_decode','legacy_setting_keeps_aboutme','pose_transition_flow','personality_flow','walk_flow','reference_flow','animation_version_rollback'])
            if state.get('animation_frame_count',0)<3: state['failed_animation_frames']=True
            required.append('animation_advances')
            state['animation_advances']=not state.get('failed_animation_frames',False)

        if rig and state.get('aboutme_installed') is False:
            required.extend(('fallback_sprite_shown', 'fallback_rig_restored'))
        required.append('qq_media_settings_ready')
        required.extend(['menu_groups_ready', 'wizard_complete', 'app_icon_ready', 'panel_controls_ready', 'settings_pages_ready', 'date_reminder_editable', 'persona_ready', 'mood_ready', 'speech_default_on'])
        required.extend(['expression_flow','expression_single_request','expression_clean_text','expression_library_ready','expression_library_background_ready','expression_history_ready','expression_trace_ready'])
        state['failed_checks'] = [key for key in required if not state.get(key)]
        if state.get('tools_listed') != 10: state['failed_checks'].append('tools_listed')
        if state.get('corner_alpha') != 0: state['failed_checks'].append('corner_alpha')
        if state.get('center_alpha', 0) < (150 if placeholder else 200): state['failed_checks'].append('center_alpha')
        if state.get('pose_after_poke') != 'poked': state['failed_checks'].append('pose_after_poke')
        if state.get('pose_grabbed') != 'grabbed': state['failed_checks'].append('pose_grabbed')
        if rig and state.get('rig_loaded') != 'ready': state['failed_checks'].append('rig_loaded')
        state['passed'] = not state['failed_checks'] and not state['errors'] and state['remaining_workers'] == 0
        (output/'report.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    QApplication.instance().aboutToQuit.connect(finish)
    return state
