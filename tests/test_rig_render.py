"""骨骼渲染页的真实渲染回归（T6）：Chromium 打开 rig.html，检查重载 / 并发加载后画面不是空的、未就绪的尾巴不转。
需要 Pillow 和 Python 版 Playwright（带 Chromium）；没有就跳过（桌宠运行不需要它们）。"""
from functools import partial
import http.server
import io
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

try:
    import PIL  # noqa: F401
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT/'src'/'aicompanion'/'assets'


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


@unittest.skipIf(sync_playwright is None, '需要 Pillow 和 Playwright')
class RigRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT/'tests')); sys.path.insert(0, str(ROOT/'tools'))
        from test_pose_pack import make_source
        from make_placeholder_pack import make
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_pack import compile_pack
        cls.tmp = tempfile.TemporaryDirectory(); site = Path(cls.tmp.name)/'site'
        shutil.copytree(ASSETS/'rig', site/'rig'); shutil.copytree(ASSETS/'live2d'/'vendor', site/'live2d'/'vendor')
        compile_posepack(make_source(Path(cls.tmp.name)/'art'), site/'pose')
        compile_pack(make(Path(cls.tmp.name)/'single', 512), site/'single')
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), partial(_Quiet, directory=str(site)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}/'
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch(args=['--use-gl=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'])
        except Exception as error:   # 装了包但没有浏览器
            cls.server.shutdown(); cls.tmp.cleanup(); raise unittest.SkipTest(f'Chromium 不可用：{error}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 240, 'height': 260})
        self.errors = []; self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.goto(self.base+'rig/rig.html')

    def tearDown(self):
        self.page.close()

    def visible(self) -> int:
        from PIL import Image
        self.page.wait_for_timeout(600)
        image = Image.open(io.BytesIO(self.page.screenshot(omit_background=True))).convert('RGBA')
        return image.getchannel('A').point(lambda a: 255 if a > 24 else 0).histogram()[255]

    def load(self, name):
        return self.page.evaluate('u => rigApi.load(u)', self.base+name+'/rig.json')

    def test_reload_same_url_keeps_textures(self):
        """Codex 复现：同一 URL 再 load 一次，旧引擎 dispose 销毁了新引擎共用的缓存纹理，画面变成全透明。"""
        for name in ('pose', 'single'):
            self.load(name); first = self.visible(); self.assertGreater(first, 150, name)
            self.load(name); self.assertGreater(self.visible(), first*0.8, name)
            self.load(name); self.assertGreater(self.visible(), first*0.8, name)
        self.assertEqual(self.errors, [])

    def test_concurrent_and_cancelled_loads(self):
        """同 URL 并发加载、加载中途换别的模型（旧加载被丢弃）：留下的模型画面正常。"""
        self.page.evaluate('u => Promise.all([rigApi.load(u), rigApi.load(u)])', self.base+'pose/rig.json')
        self.assertGreater(self.visible(), 150)
        self.page.evaluate('([a, b]) => Promise.all([rigApi.load(a), rigApi.load(b), rigApi.load(a)])',
                           [self.base+'pose/rig.json', self.base+'single/rig.json'])
        self.assertEqual(self.page.evaluate('rigApi.diagnostics().kind'), 'posepack'); self.assertGreater(self.visible(), 150)
        self.assertEqual(self.errors, [])

    def test_unready_tail_does_not_rotate(self):
        """源里 motion_ready 为空的姿态：尾巴节点相对身体的旋转严格为 0；标了就绪的 talking 才摆。"""
        self.load('pose')
        for pose in ('idle', 'sleepy', 'celebrate'):
            self.page.evaluate('p => rigApi.set({pose: p})', pose); self.page.wait_for_timeout(700)
            diag = self.page.evaluate('rigApi.diagnostics()')
            self.assertEqual(diag['swinging'], [], pose)
            self.assertAlmostEqual(diag['relative'].get('tail', 0), 0, places=9, msg=pose)
        self.page.evaluate("rigApi.set({pose: 'talking'})")
        angles = []
        for _ in range(6):
            self.page.wait_for_timeout(200); angles.append(self.page.evaluate('rigApi.diagnostics().relative.tail'))
        self.assertGreater(max(abs(a) for a in angles), 0.01); self.assertLessEqual(max(abs(a) for a in angles), 4+1e-6)


if __name__ == '__main__':
    unittest.main()
