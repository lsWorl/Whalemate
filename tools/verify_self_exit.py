"""Windows 双进程验收：真实桌宠、系统朗读、WebEngine、Harness、MCP 和 QQ 回环桥；对话用测试替身。"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

FAREWELL = '我先回去休息啦。'
RETURN = '上次我离开，是因为想休息一会儿。这会儿我回来啦。'


def run_pet(root, phase):
    from PySide6.QtCore import QTimer
    from aicompanion import app
    from aicompanion.agent.housekeeping import Report
    from aicompanion.bridge.server import runtime
    from aicompanion.config import Settings
    from aicompanion.llm.mock_client import MockLLMClient
    from aicompanion.storage.database import Database
    from aicompanion.ui.chat_worker import TaskWorker
    from dsh_test_support import temporary_dsh_home

    settings = Settings('AICompanion 自退出验收', root/'pet.sqlite3', offline=True,
                        meme_directory=root/'memes', storage_paths_file=root/'locations.json')
    db = Database(settings.database_path)
    db.initialize()
    for key, value in dict(onboarding_done=True, bridge_enabled=True, bridge_port=0, qq_root=str(root/'qq'),
                           memory_analysis=False, relationship_learning=False, proactive_enabled=False, life_enabled=False,
                           speech_enabled=True, tts_provider='system', speech_style='full').items():
        db.set_setting(key, value)
    db.set_setting('speech_language:deepseek-whale', 'zh')
    evidence = {}
    window = None

    class FarewellModel(MockLLMClient):
        def generate_reply_stream(self, messages, prompt, cancellation):
            yield FAREWELL
            yield '<lea'
            yield 've reason="想休息一会儿"/>'

    from aicompanion.ui.main_window import MainWindow

    def opened(**kwargs):
        nonlocal window
        window = MainWindow(**kwargs)
        window.agent.housekeep = Report
        window.controller.use_client(FarewellModel())
        window.speech.speaking.connect(lambda active: evidence.update(speech_started=True) if active else None)
        heard = {}
        def delivered(generation, part):
            heard[generation] = heard.get(generation, '') + part
        window.speech.delivered.connect(delivered)

        def begin():
            try:
                if phase == 'return':
                    evidence['first_bubble'] = any(b.full_text == RETURN for b in window.pet.bubble.bubbles)
                    evidence['once_only'] = not window.welcome_back()
                    QTimer.singleShot(1200, window.request_exit)
                    return
                server = runtime.server
                evidence['bridge_port'] = server.port
                evidence['bridge_listening'] = server.thread.is_alive()
                def background(token):
                    evidence['background_cancelled'] = token.event.wait(90)
                worker = TaskWorker(background, window)
                window.tasks.add(worker)
                worker.finished.connect(lambda: window._task_finished(worker))
                worker.start()
                window._web_microphone().ensure_page()
                window.agent.server.acp_entry()
                # 仅初始化 ACP，不创建模型会话或提供任何 Key。
                conn = window.agent.dsh._ensure(())
                evidence['harness_pid'] = conn.proc.pid
                evidence['app_pid'] = os.getpid()
                def wait_for_observer():
                    if not window.web_microphone.is_ready:
                        QTimer.singleShot(100, wait_for_observer)
                        return
                    (root/'ready.json').write_text(json.dumps(evidence), encoding='utf-8')
                    if (root/'observed').exists():
                        window.submit('那你先休息吧。', source='voice')
                    else:
                        QTimer.singleShot(100, wait_for_observer)
                wait_for_observer()
            except Exception as exc:
                evidence['error'] = type(exc).__name__ + ': ' + str(exc)
                window.request_exit()

        def timeout():
            evidence['timeout'] = True
            window.request_exit()
        QTimer.singleShot(0, begin)
        QTimer.singleShot(90000, timeout)
        def at_close():
            evidence['threads_drained'] = not (window.workers or window.tasks or window.speech.workers or window.agent.busy)
            evidence['voice_stopped'] = not (window.speech.busy or window.speech.queue or window.call_active)
            if phase == 'leave':
                evidence['self_exit'] = window.exit_prepared
                evidence['farewell_spoken'] = any(heard.get(g) == FAREWELL for g in window.texts)
                evidence['farewell_complete'] = any(window.texts[g] == FAREWELL and s == 'complete'
                                                    for g, s in window.reply_status.items())
            evidence['harness_closed'] = window.agent.dsh.conn is None
            thread = window.agent.server._thread
            evidence['mcp_stopped'] = thread is None or not thread.is_alive()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(at_close)
        return window

    with ExitStack() as stack:
        home = stack.enter_context(temporary_dsh_home())
        stack.enter_context(patch('aicompanion.agent.dsh_backend.dsh_home', return_value=home))
        stack.enter_context(patch.object(app, 'INSTANCE_NAME', 'AICompanion-self-exit-'+root.name))
        stack.enter_context(patch.object(app, 'load_settings', return_value=settings))
        stack.enter_context(patch.object(app, 'MainWindow', side_effect=opened))
        code = app.main()
    evidence['bridge_closed'] = runtime.server is None
    (root/(phase+'.json')).write_text(json.dumps(evidence, ensure_ascii=False), encoding='utf-8')
    return code


def processes():
    # 只取进程身份和父子关系。命令行可能带凭据，不采集。
    command = 'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress'
    rows = json.loads(subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', command]))
    return {r['ProcessId']: r for r in rows}


def family(rows, root_pid):
    ids = {root_pid}
    while True:
        children = {pid for pid, row in rows.items() if row['ParentProcessId'] in ids}
        if children <= ids:
            return ids
        ids.update(children)


def verify():
    if os.name != 'nt':
        raise SystemExit('需要 Windows 桌面。')
    with tempfile.TemporaryDirectory(prefix='whalemate-exit-') as folder:
        root = Path(folder)
        base = [sys.executable, str(Path(__file__).resolve()), '--fixture', folder, '--phase']
        pet = subprocess.Popen([*base, 'leave'])
        deadline = time.monotonic()+85
        while not (root/'ready.json').exists() and pet.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        before = processes()
        tracked = family(before, pet.pid)
        (root/'observed').touch()
        leave_code = pet.wait(timeout=100)
        leave = json.loads((root/'leave.json').read_text(encoding='utf-8'))
        after = processes()
        # 退出期间新建的子进程也要查。即便它的直接父进程已结束，系统仍保留 ParentProcessId。
        remaining = set().union(*(family(after, pid) for pid in tracked)) & after.keys()
        port = leave.get('bridge_port')
        with socket.socket() as probe:
            port_closed = bool(port and probe.connect_ex(('127.0.0.1', port)) != 0)
        run = subprocess.run([*base, 'return'], timeout=100)
        returned = json.loads((root/'return.json').read_text(encoding='utf-8'))
        names = {pid: before[pid]['Name'] for pid in tracked if pid in before}
        components = ('self_exit', 'bridge_listening', 'background_cancelled', 'threads_drained', 'voice_stopped', 'farewell_spoken',
                      'farewell_complete', 'harness_closed', 'mcp_stopped', 'bridge_closed')
        passed = (leave_code == run.returncode == 0 and not leave.get('timeout') and not remaining
                  and leave.get('harness_pid') in tracked and 'QtWebEngineProcess.exe' in names.values()
                  and port_closed and all(leave.get(k) for k in components)
                  and not returned.get('timeout') and returned.get('first_bubble') and returned.get('once_only')
                  and returned.get('threads_drained') and returned.get('voice_stopped') and returned.get('bridge_closed'))
        report = dict(passed=bool(passed), processes=names, remaining=sorted(remaining), bridge_port_closed=port_closed,
                      leave_exit=leave_code, restart_exit=run.returncode, leave=leave, restart=returned)
        output = Path(__file__).resolve().parents[1]/'output'/'v2-check'/'self-exit.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return int(not passed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--phase', choices=('leave', 'return'))
    options = parser.parse_args()
    if options.phase or options.fixture:
        if not (options.phase and options.fixture and options.fixture.resolve().parent == Path(tempfile.gettempdir()).resolve()
                and options.fixture.name.startswith('whalemate-exit-') and options.fixture.is_dir()):
            parser.error('子进程验收需要本次创建的临时目录。直接运行脚本即可开始验收。')
    sys.exit(run_pet(options.fixture, options.phase) if options.phase else verify())
