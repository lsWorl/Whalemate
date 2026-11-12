"""Windows 发布包的扫码前验收。使用临时配置、真实组件及本机回环接口。"""
import json
import os
from pathlib import Path
import socket
import time

from aicompanion.bridge.server import runtime
from aicompanion.qq.manager import QQManager, pick_port
from aicompanion.qq.napcat_webui import WebUIError


def run(database, controller, root: Path) -> int:
    # 避免第三方组件从真实账户的 AppData、用户目录或 uv 工具配置中恢复会话。
    profile = root/'profile'
    for key, path in (('USERPROFILE', profile), ('LOCALAPPDATA', profile/'AppData/Local'),
                      ('APPDATA', profile/'AppData/Roaming')):
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    database.set_setting('qq_root', str(root/'qq'))
    database.set_setting('bridge_port', pick_port(18041))
    runtime.controller = controller
    manager = QQManager(database, heartbeat=lambda: runtime.last_heartbeat)
    checks = dict(bundled_zip=False, napcat=False, webui=False, qr_png=False, astrbot=False,
                  onebot_listening=False, plugin_heartbeat=False)
    report = {'checks': checks, 'onebot_connected': False, 'passed': False}
    try:
        manager.prepare()
        component = manager.layout.manifest()['components']['napcat']
        checks['bundled_zip'] = component['source'] == '随包 ZIP'
        manager.start()
        ports = manager.ports()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            checks['napcat'] = manager.supervisor.running('napcat')
            checks['astrbot'] = manager.supervisor.running('astrbot')
            try:
                state = manager.webui().login_status()
                checks['webui'] = not state.get('isLogin')
                qr = manager.layout.napcat_home/'cache/qrcode.png'
                if qr.is_file():
                    with qr.open('rb') as handle:
                        checks['qr_png'] = handle.read(8) == b'\x89PNG\r\n\x1a\n'
            except WebUIError:
                pass
            try:
                with socket.create_connection(('127.0.0.1', ports['onebot']), timeout=1):
                    checks['onebot_listening'] = True
            except OSError:
                checks['onebot_listening'] = False
            beat = runtime.last_heartbeat or {}
            checks['plugin_heartbeat'] = bool(beat)
            report['onebot_connected'] = bool((beat.get('onebot') or {}).get('connected'))
            if all(checks.values()) or not checks['napcat'] or (checks['qr_png'] and not checks['astrbot']):
                break
            time.sleep(2)
        report['passed'] = all(checks.values())
        if not report['passed']:
            # 状态标题不包含 WebUI token、二维码链接或私聊内容。
            report['status'] = manager.diagnose().title
    except Exception as error:
        report['error'] = manager.remember_error(error)
    finally:
        try:
            manager.stop()
        finally:
            runtime.close()
    target = Path.cwd()/'bundle-qq-smoke'
    target.mkdir(exist_ok=True)
    (target/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1
