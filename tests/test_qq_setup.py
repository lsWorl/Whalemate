"""一键接入 QQ（T9 第二步）：下载校验、安装、配置生成、端口、进程保护、已有实例保护、诊断。
全部离线：下载用本机临时 HTTP 服务或假的下载函数，uv / 进程用假的，不登录 QQ、不连网、不调用模型。"""
import hashlib
import http.server
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import zipfile

from aicompanion.qq import configure, specs
from aicompanion.qq.fetch import FetchError, fetch
from aicompanion.qq.layout import Layout, install_archive, safe_extract
from aicompanion.qq.manager import QQManager, SetupError, pick_port
from aicompanion.qq.supervisor import Supervisor
from aicompanion.storage.database import Database


def make_zip(path: Path, files: dict, top: str = '') -> Path:
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items(): z.writestr(top+name, data)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class FetchTests(Base):
    def serve(self, payload: bytes):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self_):
                self_.send_response(200); self_.send_header('Content-Length', str(len(payload))); self_.end_headers(); self_.wfile.write(payload)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return f'http://127.0.0.1:{server.server_address[1]}/pkg.zip'

    def test_verified_download_and_reuse(self):
        payload = b'napcat'*1000; digest = hashlib.sha256(payload).hexdigest()
        url = self.serve(payload); seen = []
        path = fetch(url, self.root/'d'/'pkg.zip', digest, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(path.read_bytes(), payload); self.assertEqual(seen[-1], (len(payload), len(payload)))
        def offline(*a, **k): raise AssertionError('已校验的文件不应重新下载')
        self.assertEqual(fetch(url, path, digest, opener=offline), path)

    def test_hash_mismatch_leaves_nothing(self):
        url = self.serve(b'tampered')
        with self.assertRaises(FetchError) as caught: fetch(url, self.root/'pkg.zip', 'a'*64)
        self.assertIn('校验不一致', str(caught.exception))
        self.assertEqual(list(self.root.iterdir()), [])                      # 没有 .part，也没有半截文件

    def test_network_failure_and_cancel(self):
        def down(*a, **k): raise OSError('connection refused')
        with self.assertRaises(FetchError) as caught: fetch('http://x', self.root/'pkg.zip', 'a'*64, opener=down)
        self.assertIn('下载失败', str(caught.exception))
        url = self.serve(b'x'*(3 << 20))
        with self.assertRaises(FetchError): fetch(url, self.root/'big.zip', 'a'*64, cancelled=lambda: True)
        self.assertEqual(list(self.root.iterdir()), [])


class InstallTests(Base):
    spec = specs.Archive('napcat', '1.0', 'http://x/n.zip', '0'*64, 1, required=('node.exe', 'napcat/napcat.mjs'))

    def test_install_reuse_and_nested_top(self):
        layout = Layout(self.root/'qq')
        z = make_zip(self.root/'n.zip', {'node.exe': 'x', 'napcat/napcat.mjs': 'x'}, top='NapCat/')
        path = install_archive(layout, self.spec, z)
        self.assertTrue((path/'node.exe').is_file()); self.assertEqual(path, layout.root/'napcat'/'1.0')
        self.assertEqual(install_archive(layout, self.spec, self.root/'missing.zip'), path)   # 已装好的直接复用
        self.assertEqual(sorted(p.name for p in (layout.root/'napcat').iterdir()), ['1.0'])  # 没留临时目录

    def test_bad_package_keeps_previous_version(self):
        layout = Layout(self.root/'qq')
        good = install_archive(layout, self.spec, make_zip(self.root/'ok.zip', {'node.exe': 'x', 'napcat/napcat.mjs': 'x'}))
        newer = specs.Archive('napcat', '2.0', 'http://x', '0'*64, 1, required=self.spec.required)
        with self.assertRaises(ValueError): install_archive(layout, newer, make_zip(self.root/'bad.zip', {'node.exe': 'x'}))
        self.assertFalse((layout.root/'napcat'/'2.0').exists()); self.assertTrue((good/'node.exe').is_file())
        self.assertEqual(sorted(p.name for p in (layout.root/'napcat').iterdir()), ['1.0'])

    def test_zip_slip_rejected(self):
        evil = make_zip(self.root/'evil.zip', {'../outside.txt': 'x'})
        with self.assertRaises(ValueError): safe_extract(evil, self.root/'t')
        self.assertFalse((self.root/'outside.txt').exists())


class ConfigureTests(Base):
    def test_napcat_config_only_touches_our_entry(self):
        home = self.root/'napcat-home'; config = home/'config'; config.mkdir(parents=True)
        (config/'webui.json').write_text(json.dumps({'host': '::', 'port': 6099, 'token': 'old', 'theme': 'dark'}))
        user = {'name': 'user-bot', 'enable': True, 'url': 'ws://10.0.0.2:8080', 'token': 't'}
        (config/'onebot11_12345.json').write_text(json.dumps({'network': {'websocketClients': [user]}, 'musicSignUrl': 'keep'}))
        configure.napcat_config(home, 6100, 'webui-secret', 'ws://127.0.0.1:6200/ws', 'onebot-secret')
        configure.napcat_config(home, 6100, 'webui-secret', 'ws://127.0.0.1:6200/ws', 'onebot-secret')   # 重复执行
        webui = json.loads((config/'webui.json').read_text())
        self.assertEqual((webui['host'], webui['port'], webui['token'], webui['theme']), ('127.0.0.1', 6100, 'webui-secret', 'dark'))
        for name in ('onebot11.json', 'onebot11_12345.json'):
            clients = json.loads((config/name).read_text())['network']['websocketClients']
            ours = [c for c in clients if c['name'] == specs.ONEBOT_CLIENT_NAME]
            self.assertEqual(len(ours), 1); self.assertEqual((ours[0]['url'], ours[0]['token']), ('ws://127.0.0.1:6200/ws', 'onebot-secret'))
        self.assertIn(user, json.loads((config/'onebot11_12345.json').read_text())['network']['websocketClients'])
        self.assertEqual(json.loads((config/'onebot11_12345.json').read_text())['musicSignUrl'], 'keep')

    def test_astrbot_config_keeps_other_platforms(self):
        root = self.root/'astrbot'; configure.astrbot_init(root)
        self.assertTrue((root/'.astrbot').is_file() and (root/'data'/'plugins').is_dir())
        other = {'id': 'telegram', 'type': 'telegram', 'enable': True}
        (root/'data'/'cmd_config.json').write_text(json.dumps({'platform': [other], 'provider': [{'id': 'x'}]}))
        configure.astrbot_config(root, 6200, 'onebot-secret', 6186); configure.astrbot_config(root, 6201, 'onebot-secret', 6186)
        conf = json.loads((root/'data'/'cmd_config.json').read_text())
        ours = [p for p in conf['platform'] if p['id'] == specs.PLATFORM_ID]
        self.assertEqual(len(ours), 1); self.assertEqual((ours[0]['ws_reverse_host'], ours[0]['ws_reverse_port']), ('127.0.0.1', 6201))
        self.assertIn(other, conf['platform']); self.assertEqual(conf['provider'], [{'id': 'x'}])
        self.assertEqual(conf['dashboard'], {'host': '127.0.0.1', 'port': 6186})

    def test_broken_json_is_not_overwritten(self):
        root = self.root/'astrbot'; configure.astrbot_init(root)
        (root/'data'/'cmd_config.json').write_text('{broken')
        with self.assertRaises(configure.ConfigError): configure.astrbot_config(root, 1, 't', 2)
        self.assertEqual((root/'data'/'cmd_config.json').read_text(), '{broken')

    def test_plugin_install_update_and_protection(self):
        root = self.root/'astrbot'; configure.astrbot_init(root)
        source = configure.plugin_source()
        target = configure.install_plugin(root, source)
        self.assertTrue(configure.is_our_plugin(target)); self.assertTrue((target/'main.py').is_file())
        (target/'stale.txt').write_text('old'); configure.install_plugin(root, source)   # 更新：整体替换
        self.assertFalse((target/'stale.txt').exists())
        configure.plugin_config(root, 'http://127.0.0.1:18765', 'tok')
        path = root/'data'/'config'/f'{specs.PLUGIN_NAME}_config.json'
        data = json.loads(path.read_text()); data['offline_reply'] = '我的离线提示'; path.write_text(json.dumps(data))
        configure.plugin_config(root, 'http://127.0.0.1:19000', 'tok2')
        data = json.loads(path.read_text())
        self.assertEqual((data['pet_url'], data['token'], data['offline_reply']), ('http://127.0.0.1:19000', 'tok2', '我的离线提示'))
        # 同名但不是我们装的：拒绝，原样保留
        other = self.root/'other'; configure.astrbot_init(other)
        foreign = other/'data'/'plugins'/specs.PLUGIN_NAME; foreign.mkdir(); (foreign/'main.py').write_text('user code')
        with self.assertRaises(configure.ConfigError): configure.install_plugin(other, source)
        self.assertEqual((foreign/'main.py').read_text(), 'user code')

    def test_failed_plugin_update_keeps_old(self):
        root = self.root/'astrbot'; configure.astrbot_init(root)
        target = configure.install_plugin(root, configure.plugin_source())
        broken = self.root/'broken'; broken.mkdir(); (broken/'main.py').write_text('x')
        (broken/'sub').mkdir()
        from unittest import mock
        with mock.patch('aicompanion.qq.configure.shutil.copytree', side_effect=OSError('磁盘满')):
            with self.assertRaises(OSError): configure.install_plugin(root, broken)
        self.assertTrue(configure.is_our_plugin(target)); self.assertIn('AICompanionBridge', (target/'main.py').read_text())
        self.assertEqual([p.name for p in (root/'data'/'plugins').iterdir()], [specs.PLUGIN_NAME])


class FakeProcess:
    def __init__(self, code=None, pid=4242): self.code, self.pid = code, pid
    def poll(self): return self.code
    def wait(self, timeout=None): return self.code


class ManagerBase(Base):
    def setUp(self):
        super().setUp()
        self.db = Database(self.root/'pet.sqlite3'); self.db.initialize()
        self.db.set_setting('qq_root', str(self.root/'qq'))
        self.calls = []; self.heartbeat = None; self.now = 1000.0
        self.zips = {specs.NAPCAT.url: make_zip(self.root/'napcat.zip', {r: 'x' for r in specs.NAPCAT.required}),
                     specs.UV.url: make_zip(self.root/'uv.zip', {'uv.exe': 'x'})}
        self.downloads = []
        def fetcher(url, dest, sha, progress=None, cancelled=None):
            self.downloads.append(url); return self.zips[url]
        self.free_ports = set()
        self.qq = QQManager(self.db, heartbeat=lambda: self.heartbeat, run=self.fake_run, fetcher=fetcher,
                            free=lambda port: port not in self.free_ports, clock=lambda: self.now)
        self.qq.allow_non_windows = True
        from aicompanion.bridge.server import runtime
        self.runtime = runtime; self.addCleanup(lambda: setattr(runtime, 'server', None))
        runtime.server = object()   # 桥接接口"已打开"

    fail_on = None

    def fake_run(self, args, **kwargs):
        self.calls.append(args[1:])
        if self.fail_on and self.fail_on in args: return subprocess.CompletedProcess(args, 1, '', 'network unreachable')
        if args[1] == 'venv':
            scripts = Path(args[2])/'Scripts'; scripts.mkdir(parents=True); (scripts/'astrbot.exe').write_text('x'); (scripts/'python.exe').write_text('x')
        out = f'astrbot=={specs.ASTRBOT.version}\naiohttp==3.10.0\n' if args[1:3] == ['pip', 'freeze'] else ''
        return subprocess.CompletedProcess(args, 0, out, '')


class PrepareTests(ManagerBase):
    def test_prepare_writes_everything_and_is_repeatable(self):
        self.qq.prepare()
        manifest = self.qq.layout.manifest()['components']
        self.assertEqual(manifest['napcat']['sha256'], specs.NAPCAT.sha256)
        self.assertEqual(manifest['astrbot']['version'], specs.ASTRBOT.version); self.assertIn('aiohttp==3.10.0', manifest['astrbot']['freeze'])
        self.assertEqual(self.qq.ready(), [])
        keys = self.db.get_setting('qq_secrets'); ports = self.db.get_setting('qq_ports')
        self.assertEqual(len({keys['onebot'], keys['webui'], keys['dashboard'], self.qq.bridge()[1]}), 4)   # 四个独立的随机令牌
        astrbot = json.loads((self.qq.layout.astrbot_home/'data'/'cmd_config.json').read_text())
        ours = next(p for p in astrbot['platform'] if p['id'] == specs.PLATFORM_ID)
        self.assertEqual((ours['ws_reverse_port'], ours['ws_reverse_token']), (ports['onebot'], keys['onebot']))
        onebot = json.loads((self.qq.layout.napcat_home/'config'/'onebot11.json').read_text())['network']['websocketClients'][0]
        self.assertEqual((onebot['url'], onebot['token']), (f'ws://127.0.0.1:{ports["onebot"]}/ws', keys['onebot']))
        plugin = json.loads((self.qq.layout.astrbot_home/'data'/'config'/f'{specs.PLUGIN_NAME}_config.json').read_text())
        self.assertEqual(plugin['token'], self.qq.bridge()[1])
        installs = sum(1 for c in self.calls if c[:2] == ['pip', 'install'])
        self.qq.prepare()                                                             # 重复安装：复用，不重装 AstrBot
        self.assertEqual(sum(1 for c in self.calls if c[:2] == ['pip', 'install']), installs)
        self.assertEqual(json.loads(json.dumps(self.db.get_setting('qq_secrets'))), keys)   # 令牌不变

    def test_failed_astrbot_install_keeps_previous(self):
        self.qq.prepare(); before = self.qq.layout.manifest()['components']['astrbot']
        components = self.qq.layout.manifest()['components']; components['astrbot']['version'] = 'old'
        self.qq.layout.update_manifest(components=components)                       # 假装要升级
        self.fail_on = 'install'
        with self.assertRaises(SetupError) as caught: self.qq.prepare()
        self.assertIn('network unreachable', str(caught.exception))
        after = self.qq.layout.manifest()['components']['astrbot']
        self.assertEqual(after['path'], before['path']); self.assertTrue(Path(before['path']).is_dir())   # 旧环境还在、仍在用
        self.assertEqual(len(list((self.qq.layout.root/'astrbot').iterdir())), 1)                         # 失败的新环境已清掉

    def test_port_conflicts_are_avoided(self):
        self.free_ports = {6199, 6099}   # 这两个被别的程序占了
        ports = self.qq.ports()
        self.assertNotIn(ports['onebot'], (6199, 6099)); self.assertNotIn(ports['webui'], (6199, 6099))
        self.assertEqual(len(set(ports.values())), 3)
        self.free_ports = {ports['dashboard']}                                        # 后来被占：重新配置时换掉
        self.assertNotEqual(self.qq.ports(reassign=True)['dashboard'], ports['dashboard'])
        self.assertEqual(pick_port(7000, taken=[7000], free=lambda p: True), 7001)


class ProcessTests(ManagerBase):
    def test_start_failure_is_reported_with_redacted_log(self):
        self.qq.prepare()
        secret = self.qq.secrets()['webui']
        def popen(args, **kwargs):
            kwargs['stdout'].write(f'WebUi User Panel Url: http://127.0.0.1:6099/webui?token={secret}\n'.encode())
            return FakeProcess(code=1)
        self.qq.supervisor = Supervisor(self.qq.layout, path_of=lambda pid: None, popen=popen)
        self.qq.start()
        status = self.qq.diagnose()
        self.assertEqual(status.code, 'start_failed'); self.assertNotIn(secret, status.detail); self.assertIn('***', status.detail)
        self.assertNotIn(secret, self.qq.diagnostics_text())

    def test_never_kills_processes_that_are_not_ours(self):
        layout = Layout(self.root/'qq'); killed = []
        layout.update_manifest(processes={'napcat': {'pid': 999}})
        sup = Supervisor(layout, path_of=lambda pid: r'C:\Windows\explorer.exe')     # pid 已被别的程序复用
        import aicompanion.qq.supervisor as module
        original = module.subprocess.run; module.subprocess.run = lambda *a, **k: killed.append(a)
        try: self.assertFalse(sup.stop('napcat'))
        finally: module.subprocess.run = original
        self.assertEqual(killed, []); self.assertFalse(sup.running('napcat'))

    def test_start_requires_prepared_components(self):
        self.db.set_setting('qq_mode', 'auto')
        with self.assertRaises(SetupError) as caught: self.qq.start()
        self.assertIn('NapCat', str(caught.exception))


class ExistingInstanceTests(ManagerBase):
    def test_attach_only_touches_our_plugin(self):
        root = self.root/'MyAstrBot'; configure.astrbot_init(root)
        cmd = {'platform': [{'id': 'mine', 'type': 'aiocqhttp'}], 'provider': [{'id': 'gpt'}]}
        (root/'data'/'cmd_config.json').write_text(json.dumps(cmd))
        other = root/'data'/'plugins'/'someone_else'; other.mkdir(); (other/'main.py').write_text('keep')
        (root/'data'/'config'/'someone_else_config.json').write_text('{"a":1}')
        self.qq.attach_existing(root)
        self.assertEqual(json.loads((root/'data'/'cmd_config.json').read_text()), cmd)
        self.assertEqual((other/'main.py').read_text(), 'keep'); self.assertEqual((root/'data'/'config'/'someone_else_config.json').read_text(), '{"a":1}')
        self.assertTrue(configure.is_our_plugin(root/'data'/'plugins'/specs.PLUGIN_NAME))
        self.assertEqual(self.db.get_setting('qq_mode'), 'existing')
        with self.assertRaises(SetupError): self.qq.attach_existing(self.root/'not-astrbot')


class DiagnoseTests(ManagerBase):
    class WebUI:
        state = {'isLogin': False}
        def __init__(self, port, token): pass
        def login_status(self): return self.state
        def login_info(self): return {'uin': '88888888'}
        def refresh_qrcode(self): pass

    def test_status_sequence(self):
        self.assertEqual(self.qq.diagnose().code, 'not_setup')
        self.db.set_setting('qq_mode', 'auto')
        self.assertEqual(self.qq.diagnose().code, 'not_ready')
        self.qq.prepare()
        self.qq.supervisor = Supervisor(self.qq.layout, path_of=lambda pid: None, popen=lambda args, **k: FakeProcess())
        self.assertEqual(self.qq.diagnose().code, 'stopped')
        self.qq.start(); self.qq.webui_factory = self.WebUI; self.qq._webui = None
        qr = self.qq.layout.napcat_home/'cache'/'qrcode.png'; qr.parent.mkdir(parents=True, exist_ok=True); qr.write_bytes(b'png')
        status = self.qq.diagnose(); self.assertEqual((status.code, status.qrcode), ('need_login', str(qr)))
        self.WebUI.state = {'isLogin': True}
        self.assertEqual(self.qq.diagnose().code, 'plugin_missing')
        self.assertEqual(self.db.get_setting('qq_bot_uin'), '88888888')             # 记下机器人号，下次快速登录
        self.heartbeat = {'at': self.now, 'onebot': {'connected': False, 'error': 'ApiNotAvailable'}, 'groups': []}
        self.assertEqual(self.qq.diagnose().code, 'onebot_down')
        self.heartbeat = {'at': self.now, 'onebot': {'connected': True, 'self_id': '88888888'}, 'groups': [{'id': '30003', 'name': '测试群'}]}
        status = self.qq.diagnose(); self.assertEqual((status.code, status.groups[0]['id']), ('need_owner', '30003'))
        self.db.set_setting('bridge_owner:qq', '10001')
        self.assertEqual(self.qq.diagnose().code, 'connected')
        self.now += 120                                                               # 心跳过期
        self.assertEqual(self.qq.diagnose().code, 'plugin_missing')
        self.runtime.server = None
        self.assertEqual(self.qq.diagnose().code, 'bridge_off')
        self.WebUI.state = {'isLogin': False}


class HeartbeatTests(Base):
    def test_heartbeat_records_state_and_returns_allowed_groups(self):
        import urllib.request
        from aicompanion.bridge.server import BridgeServer, runtime
        db = Database(self.root/'pet.sqlite3'); db.initialize(); db.set_setting('bridge_groups', ['30003'])
        service = type('S', (), {'database': db, 'controller': None, 'closed': False})()
        server = BridgeServer(service, 0, 'tok')
        try:
            body = json.dumps({'astrbot': 'x', 'onebot': {'connected': True, 'self_id': '888'},
                               'groups': [{'id': '30003', 'name': '测试群'}, {'id': 'bad id', 'name': 'x'}]}).encode()
            request = urllib.request.Request(f'http://127.0.0.1:{server.port}/v1/heartbeat', data=body,
                                             headers={'Authorization': 'Bearer tok', 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5) as response: reply = json.loads(response.read())
        finally:
            server.close()
        self.assertEqual(reply, {'ok': True, 'groups': ['30003']})
        self.assertEqual(runtime.last_heartbeat['groups'], [{'id': '30003', 'name': '测试群'}])   # 不合法的群号丢掉
        self.assertTrue(runtime.last_heartbeat['onebot']['connected'])


if __name__ == '__main__':
    unittest.main()
