"""AstrBot 启动方式：绕开被 Windows 智能应用控制拦截的虚拟环境启动器（4551）。全部离线，用合成目录和假的进程。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aicompanion.qq.launch import BOOT, astrbot_command, base_python
from aicompanion.qq.layout import Layout
from aicompanion.qq.supervisor import Supervisor
from test_qq_setup import FakeProcess, ManagerBase


def fake_venv(root: Path, home: Path, *, cfg=True) -> Path:
    venv = root/'astrbot'/'4.29.3-abc'
    (venv/'Scripts').mkdir(parents=True); (venv/'Scripts'/'astrbot.exe').write_text('x', encoding='utf-8')
    if cfg: (venv/'pyvenv.cfg').write_text(f'home = {home}\nimplementation = CPython\nversion_info = 3.12\n', encoding='utf-8')
    return venv


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)/'qq'; self.layout = Layout(self.root)
        self.home = self.root/'python'/'cpython-3.12-windows-x86_64-none'; self.home.mkdir(parents=True)
        (self.home/'python.exe').write_text('x', encoding='utf-8')
    def tearDown(self): self.tmp.cleanup()

    def test_uses_base_interpreter_isolated_with_utf8(self):
        venv = fake_venv(self.root, self.home)
        command = astrbot_command(venv, self.layout, 'run')
        self.assertEqual(command[0], self.home/'python.exe')
        self.assertEqual(command[1:5], ['-I', '-X', 'utf8', '-c']); self.assertEqual(command[5], BOOT)
        self.assertEqual(command[6:], [venv, 'run'])
        self.assertNotIn('astrbot.exe', ' '.join(map(str, command)))

    def test_falls_back_to_original_launcher_when_base_is_unusable(self):
        # 没有 pyvenv.cfg / home 指到组件目录以外 / 基础解释器不存在：都退回原来的 Scripts\astrbot.exe
        venv = fake_venv(self.root, self.home, cfg=False)
        self.assertEqual(astrbot_command(venv, self.layout, 'run'), [venv/'Scripts'/'astrbot.exe', 'run'])
        outside = Path(self.tmp.name)/'elsewhere'; outside.mkdir(); (outside/'python.exe').write_text('x', encoding='utf-8')
        (venv/'pyvenv.cfg').write_text(f'home = {outside}\n', encoding='utf-8')
        self.assertIsNone(base_python(venv, self.layout))
        (venv/'pyvenv.cfg').write_text(f'home = {self.root/"python"/"missing"}\n', encoding='utf-8')
        self.assertIsNone(base_python(venv, self.layout))
        self.assertEqual(astrbot_command(venv, self.layout, 'run')[0], venv/'Scripts'/'astrbot.exe')

    def test_boot_really_runs_console_entry_from_venv_site_packages(self):
        # 真的执行 BOOT：合成的 astrbot 包只放在"虚拟环境"的 site-packages 里，-I 下也能按入口元数据找到并收到参数
        venv = Path(self.tmp.name)/'venv'; site = venv/'Lib'/'site-packages'
        (site/'astrbot_fake').mkdir(parents=True)
        (site/'astrbot_fake'/'__init__.py').write_text(
            'import sys\ndef cli():\n    print("ARGS=" + "|".join(sys.argv), flush=True)\n    return 0\n', encoding='utf-8')
        dist = site/'astrbot-4.29.3.dist-info'; dist.mkdir()
        (dist/'METADATA').write_text('Metadata-Version: 2.1\nName: astrbot\nVersion: 4.29.3\n', encoding='utf-8')
        (dist/'entry_points.txt').write_text('[console_scripts]\nastrbot = astrbot_fake:cli\n', encoding='utf-8')
        result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', BOOT, str(venv), 'run'],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ARGS=astrbot|run', result.stdout)


class ManagerStartTests(ManagerBase):
    def test_start_launches_astrbot_through_base_interpreter(self):
        self.qq.prepare()
        venv = self.qq.layout.current('astrbot')
        home = self.qq.layout.python_dir/'cpython-3.12-windows-x86_64-none'; home.mkdir(parents=True, exist_ok=True)
        (home/'python.exe').write_text('x', encoding='utf-8')
        (venv/'pyvenv.cfg').write_text(f'home = {home}\n', encoding='utf-8')
        launched = {}
        def popen(args, **kwargs):
            launched[Path(args[0]).name if 'node' in args[0] else 'astrbot'] = (args, kwargs)
            return FakeProcess()
        self.qq.supervisor = Supervisor(self.qq.layout, identity_of=lambda pid: None, popen=popen)
        self.qq.start()
        args, kwargs = launched['astrbot']
        self.assertEqual(args[0], str(home/'python.exe')); self.assertEqual(args[1:4], ['-I', '-X', 'utf8'])
        self.assertEqual(args[-2:], [str(venv), 'run'])
        self.assertEqual(kwargs['cwd'], str(self.qq.layout.astrbot_home))
        self.assertEqual(kwargs['env']['ASTRBOT_ROOT'], str(self.qq.layout.astrbot_home))
        self.assertIn('node.exe', launched)   # NapCat 的启动方式不变


if __name__ == '__main__':
    unittest.main()
