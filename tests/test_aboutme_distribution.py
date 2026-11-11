"""发布时使用角色定义，第三方贴图保留在安装者本机。"""
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile
import tempfile
import unittest
from unittest.mock import patch

from tools import build_windows
from tools.build_windows import archive_whale_assets, character_data_args


class AboutmeDistributionTests(unittest.TestCase):
    def test_pyinstaller_receives_only_character_definitions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            whale = root/'src/aicompanion/characters/deepseek-whale'
            for name in ('animation-aboutme/idle.webp', 'animation-original/poster.png',
                         'animation-reference-v1/animations.json', 'rig/textures/body.png',
                         'character.json', 'lines.json', 'interjections.json', 'qq-chat-card.json'):
                resource = whale/name
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.touch()
            arguments = character_data_args(root)
            self.assertEqual(arguments[::2], ['--add-data']*4)
            self.assertEqual(set(arguments[1::2]), {
                f'{whale/name}:aicompanion/characters/deepseek-whale'
                for name in ('character.json', 'lines.json', 'interjections.json', 'qq-chat-card.json')})

    def test_upgrade_recovers_previous_installations_outside_dist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root/'dist/AICompanion'
            whale = output/'_internal/aicompanion/characters/deepseek-whale'
            for name in ('animation-aboutme', 'animation-original', 'animation-reference-v1', 'rig', 'sprites'):
                resource = whale/name/'fixture'
                resource.parent.mkdir(parents=True)
                resource.write_bytes(name.encode())
            cache = output/'output/aboutme-cache'
            cache.mkdir(parents=True)
            (cache/'idle.webp').touch()
            unrelated = whale/'lines.json'
            unrelated.touch()
            backups = root/'backup'
            archive_whale_assets(output, backups)
            archive_whale_assets(output, backups)
            backup, = backups.iterdir()
            self.assertEqual(list(whale.iterdir()), [unrelated])
            self.assertFalse(cache.exists())
            self.assertTrue((backup/'aboutme-cache/idle.webp').is_file())
            for name in ('animation-aboutme', 'animation-original', 'animation-reference-v1', 'rig', 'sprites'):
                self.assertEqual((backup/name/'fixture').read_bytes(), name.encode())

    def test_release_contains_required_assets_credits_and_installer(self):
        """用临时发布目录检查完整的收集和发布步骤，替代耗时的 exe 编译。"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root/'build/release-staging/AICompanion'
            output = root/'dist/AICompanion'
            files = ['LICENSE', 'README.md', 'README.en.md', '.env.example',
                     'docs/aboutme-pet.md', 'docs/implementation_status.md', 'docs/live2d_model_sources.md',
                     'tools/install_aboutme_pet.py', 'tools/aboutme-requirements.txt', 'tools/aboutme-pet.lock.json']
            assets = ['icons/aicompanion.ico', 'live2d/index.html', 'rig/rig.html'] + [
                f'expression/{pose}.png' for pose in ('poked','sleepy','error','celebrate','thinking','wait')]
            for name in files + ['src/aicompanion/assets/'+name for name in assets]:
                target = root/name
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes(name.encode())

            def collect_data(args, **kwargs):
                staging.mkdir(parents=True)
                (staging/'AICompanion.exe').touch()
                for i, arg in enumerate(args):
                    if arg == '--add-data':
                        source, destination = args[i+1].rsplit(':', 1)
                        src = Path(source)
                        dest = staging/'_internal'/destination
                        if src.is_dir():
                            shutil.copytree(src, dest, dirs_exist_ok=True)
                        else:
                            dest.mkdir(exist_ok=True, parents=True)
                            shutil.copy2(src, dest/src.name)

            with patch.object(build_windows, 'ROOT', root), patch('sys.argv', ['build_windows.py']), \
                    patch.object(build_windows.subprocess, 'run', side_effect=collect_data):
                build_windows.main()
            for name in ('LICENSE', 'docs/aboutme-pet.md', 'tools/install_aboutme_pet.py',
                         'tools/aboutme-pet.lock.json', 'tools/aboutme-requirements.txt'):
                self.assertEqual((output/name).read_bytes(), (root/name).read_bytes())
            for name in assets:
                packaged = output/'_internal/aicompanion/assets'/name
                self.assertEqual(packaged.read_bytes(), (root/'src/aicompanion/assets'/name).read_bytes())

    def test_incremental_distribution_ignores_old_build_and_manifest(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('pyproject.toml', 'setup.py', 'MANIFEST.in'):
                shutil.copyfile(project/name, root/name)
            stale = 'aicompanion/characters/deepseek-whale/animation-aboutme/stale.webp'
            reference = 'art/deepseek-whale/reference-motion-v1/user-identity-reference.png'
            metadata = root/'src/aicompanion.egg-info'
            metadata.mkdir(parents=True)
            for name in ('src/aicompanion/__init__.py', 'src/'+stale, 'build/lib/'+stale, reference,
                         'src/aicompanion/characters/deepseek-whale/character.json'):
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            (metadata/'SOURCES.txt').write_text('src/'+stale+'\n'+reference+'\n', encoding='utf-8')
            out = root/'packages'
            out.mkdir()
            script = ('try:\n import setuptools, wheel\nexcept ImportError:\n raise SystemExit(77)\n'
                      'from setuptools import build_meta; '
                      'build_meta.build_wheel("packages"); build_meta.build_sdist("packages")')
            result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True)
            if result.returncode == 77:
                self.skipTest('构建 wheel/sdist 需要开发环境的 setuptools 和 wheel')
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            with zipfile.ZipFile(next(out.glob('*.whl'))) as wheel:
                built_files = wheel.namelist()
            with tarfile.open(next(out.glob('*.tar.gz'))) as sdist:
                source_files = sdist.getnames()
            for names in (built_files, source_files):
                self.assertTrue(any(name.endswith('characters/deepseek-whale/character.json') for name in names))
                self.assertFalse(any(name.endswith((stale, reference)) for name in names))
            self.assertTrue((root/'src'/stale).exists())
