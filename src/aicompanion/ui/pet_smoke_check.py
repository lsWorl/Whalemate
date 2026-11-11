"""v2 桌宠自检：常驻显示、原生立绘、透明与穿透遮罩、状态切换、气泡、交互与缩放。

只用临时数据库和离线模型；不打开麦克风、不联网。每个姿势截图保存到 output，便于人工复核。
"""
import json
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from aicompanion.ui.sprite_view import SpriteAvatar
try:
    from aicompanion.body.rig_view import RigAvatar
except ImportError:  # pragma: no cover
    RigAvatar = SpriteAvatar
NATIVE = (SpriteAvatar, RigAvatar)


def schedule_pet_smoke_check(window, database, output):
    output.mkdir(parents=True, exist_ok=True)
    state = {'errors': [], 'poses_seen': []}
    pet = window.pet
    pet.input_locked = True   # 自检不受手动拖动、点菜单影响
    # 记录台词标注、用户气泡、活动图标的调用，验证 P2 链路
    state['cues'] = []; state['user_bubbles'] = 0; state['activities'] = []
    original_perform, original_user, original_activity = pet.perform, pet.show_user, pet.show_activity
    pet.perform = lambda cue: (state['cues'].append(f'{cue.kind}:{cue.name}'), original_perform(cue))[1]
    pet.show_user = lambda text: (state.update(user_bubbles=state['user_bubbles']+1), original_user(text))[1]
    pet.show_activity = lambda kind='thinking': (state['activities'].append(kind), original_activity(kind))[1]
    rig = isinstance(pet.view, RigAvatar)
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
        state['native_sprites'] = isinstance(pet.view, NATIVE)
        state['webengine_not_created'] = window.web_avatar is None
        state['always_on_top'] = bool(pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        state['translucent'] = pet.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        image = pet.grab().toImage()
        state['corner_alpha'] = image.pixelColor(1, 1).alpha()
        state['center_alpha'] = image.pixelColor(image.width()//2, int(image.height()*.55)).alpha()
        if rig:
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

    def switch_role():
        # 切到一个用户自建的角色（没有形象包），再切回鲸娘
        from aicompanion.domain.character import CharacterProfile
        database.add_character(CharacterProfile('自检角色', '用于自检的临时角色', '简短回答。', 'smoke-other'))
        window._switch_to('smoke-other')
        state['switched_to_other'] = window.controller.character.id == 'smoke-other'
        window._switch_to('deepseek-whale')
        state['switched_back'] = isinstance(pet.view, NATIVE) and window.controller.character.id == 'deepseek-whale'
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
        dialog = SettingsWindow(window.controller, window.settings, window, page='models')
        try:
            state['settings_pages_ready'] = (dialog.nav.current() == 'models'
                                             and all(dialog.open_page(key) for key in SettingsWindow.PAGES))
            # 计划书 v4 功能二：人格编辑器能载入当前人格
            state['persona_ready'] = bool(dialog.persona_editor.current_id) and bool(dialog.persona_editor.description.toPlainText())
            # 计划书 v4 功能三：心情卡能读出当前心情
            state['mood_ready'] = bool(dialog.mood_card.summary.text())
            if hasattr(dialog.library_page, 'analyzer') and dialog.library_page.analyzer is not None:
                dialog.library_page.analyzer.wait(5000)
        finally:
            dialog.reject(); dialog.deleteLater()
    later(21600, ui_refresh)

    def expression_start():
        import re
        from uuid import uuid4
        original=window.controller.router
        class ExpressionModel:
            calls=0
            def generate_reply_stream(self,history,prompt,cancellation):
                self.calls+=1
                handle=re.search(r'^(s\d+)｜',prompt,re.M).group(1)   # 本轮候选短编号
                yield '<sticker id="'+handle+'"/>'
                yield '【开心】好耶，表达自检完成！<挥手>'
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
                state['expression_trace_ready']=state['expression_trace_stage']=='OK_displayed'
                from aicompanion.ui.library_page import LibraryPage
                page=LibraryPage(database,window.controller.character.id)
                state['expression_library_ready']=page.table.rowCount()>=6
                # 真正走两次后台刷新，覆盖线程已销毁后再次导入 / 刷新无反馈的问题。
                def library_repeated():
                    try:
                        page.scan()
                        page.worker.finished.connect(library_finished)
                    except Exception as error:state['errors'].append('图库重复任务失败：'+str(error));page.deleteLater()
                def library_finished():
                    state['expression_library_background_ready']=page.worker is None and '处理完成' in page.status.text()
                    page.deleteLater()
                page.scan();page.worker.finished.connect(library_repeated)
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
            QTimer.singleShot(200,restored)
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
        try: offer_and_quota()
        finally: QTimer.singleShot(1500, window.request_exit)
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
        required = ['pet_visible', 'chat_hidden', 'native_sprites', 'webengine_not_created', 'always_on_top', 'translucent',
                    'mask_active', 'mask_excludes_corner', 'bubble_after_poke', 'scale_keeps_feet', 'hidden_by_user', 'summoned',
                    'geometry_saved', 'chat_flow', 'forced_ok', 'bubble_text_fits', 'fell', 'landed_on_ground', 'reply_saved_clean', 'cues_performed', 'user_bubble_shown', 'thinking_icon', 'interaction_memory', 'purpose_started', 'purpose_delivered', 'outreach_logged', 'voice_page_ready', 'voice_sink_web', 'voice_sink_restored', 'agent_session_ready', 'task_offer_shown', 'quota_blocks_model', 'switched_to_other', 'switched_back']
        required.extend(['env_blocked', 'tools_gate', 'housekeep_ok'])
        required.append('storage_locations_ready')

        required.append('qq_media_settings_ready')
        required.extend(['app_icon_ready', 'panel_controls_ready', 'settings_pages_ready', 'persona_ready', 'mood_ready', 'speech_default_on'])
        required.extend(['expression_flow','expression_single_request','expression_clean_text','expression_library_ready','expression_library_background_ready','expression_history_ready','expression_trace_ready'])
        state['failed_checks'] = [key for key in required if not state.get(key)]
        if state.get('tools_listed') != 10: state['failed_checks'].append('tools_listed')
        if state.get('corner_alpha') != 0: state['failed_checks'].append('corner_alpha')
        if state.get('center_alpha', 0) < 200: state['failed_checks'].append('center_alpha')
        if state.get('pose_after_poke') != 'poked': state['failed_checks'].append('pose_after_poke')
        if state.get('pose_grabbed') != 'grabbed': state['failed_checks'].append('pose_grabbed')
        if rig and state.get('rig_loaded') != 'ready': state['failed_checks'].append('rig_loaded')
        state['passed'] = not state['failed_checks'] and not state['errors'] and state['remaining_workers'] == 0
        (output/'report.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    QApplication.instance().aboutToQuit.connect(finish)
    return state
