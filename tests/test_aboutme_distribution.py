"""发布时包含自绘鲸娘和角色定义，Aboutme 贴图保留在安装者本机。"""
import json
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
from tools.build_windows import archive_whale_assets


WHALE_DIR = Path('src/aicompanion/characters/deepseek-whale')
RUNTIME_FILES = ('rig/rig.json', 'rig/textures/idle/body.png', 'rig/textures/idle/blink.png',
                 'sprites/sprites.json', 'sprites/idle.png', 'sprites/blink.png')
LOCAL_FILES = ('animation-aboutme/idle.webp', 'animation-original/poster.png',
               'animation-reference-v1/animations.json', 'rig/textures/idle/unlisted.png', 'sprites/unlisted.png')


def make_whale(root):
    """合成有基础图层和闭眼差分的角色，旁置本机贴图以验证发布边界。"""
    whale = root/WHALE_DIR
    for name in ('character.json',) + RUNTIME_FILES + LOCAL_FILES:
        path = whale/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    rig = {'format': 'AICompanionPoseRig', 'version': 1, 'poses': {
        'idle': {'parts': [{'tex': 'textures/idle/body.png'}],
                 'variants': {'eyes_closed': [{'tex': 'textures/idle/blink.png'}]}}}}
    sprites = {'format': 'AICompanionSpritePack', 'version': 1, 'poses': {
        'idle': {'base': 'idle.png', 'eyes_closed': 'blink.png'}}}
    (whale/'rig/rig.json').write_text(json.dumps(rig), encoding='utf-8')
    (whale/'sprites/sprites.json').write_text(json.dumps(sprites), encoding='utf-8')
    return whale


class AboutmeDistributionTests(unittest.TestCase):
    def test_bundled_whale_art_loads_all_poses_and_textures(self):
        from aicompanion.body.rig_import import load_rig
        from aicompanion.characters import PACKS_DIR, load_pack, load_sprite_manifest

        whale = load_pack(PACKS_DIR/'deepseek-whale')
        rig = load_rig(whale.root/whale.renderer['rig']['path'])
        sprites = load_sprite_manifest(whale.sprite_manifest_path())
        poses = set('idle talking thinking working celebrate error wait sleepy grabbed poked'.split())
        self.assertEqual(set(rig['poses']), poses)
        self.assertEqual(set(sprites['poses']), poses)

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
            whale = make_whale(root)
            staging = root/'build/release-staging/AICompanion'
            output = root/'dist/AICompanion'
            installed_whale = output/'_internal'/WHALE_DIR.relative_to('src')
            stale = installed_whale/'sprites/unlisted.png'
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b'previous installation')
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
            for name in RUNTIME_FILES:
                self.assertEqual((installed_whale/name).read_bytes(), (whale/name).read_bytes())
            for name in LOCAL_FILES:
                self.assertFalse((installed_whale/name).exists(), name)
            for name in assets:
                packaged = output/'_internal/aicompanion/assets'/name
                self.assertEqual(packaged.read_bytes(), (root/'src/aicompanion/assets'/name).read_bytes())

    def test_incremental_distribution_ignores_old_build_and_manifest(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'tools').mkdir()
            for name in ('pyproject.toml', 'setup.py', 'MANIFEST.in', 'tools/whale_art_files.py'):
                shutil.copyfile(project/name, root/name)
            whale = make_whale(root)
            local = [(WHALE_DIR.relative_to('src')/name).as_posix() for name in LOCAL_FILES]
            reference = 'art/deepseek-whale/reference-motion-v1/user-identity-reference.png'
            metadata = root/'src/aicompanion.egg-info'
            metadata.mkdir(parents=True)
            for name in ['src/aicompanion/__init__.py', reference] + ['build/lib/'+file for file in local]:
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            cached_paths = ['src/'+file for file in local] + [reference]
            (metadata/'SOURCES.txt').write_text('\n'.join(cached_paths)+'\n', encoding='utf-8')
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
                for name in RUNTIME_FILES:
                    member = (WHALE_DIR.relative_to('src')/name).as_posix()
                    self.assertEqual(wheel.read(member), (whale/name).read_bytes())
            with tarfile.open(next(out.glob('*.tar.gz'))) as sdist:
                source_files = sdist.getnames()
                # 源码包还需携带自己的构建工具，才能在解压后重新构建 wheel。
                self.assertTrue(any(name.endswith('/tools/whale_art_files.py') for name in source_files))
                for name in RUNTIME_FILES:
                    suffix = '/'+(WHALE_DIR/name).as_posix()
                    member, = [path for path in source_files if path.endswith(suffix)]
                    self.assertEqual(sdist.extractfile(member).read(), (whale/name).read_bytes())
            for names in (built_files, source_files):
                self.assertTrue(any(name.endswith('characters/deepseek-whale/character.json') for name in names))
                self.assertFalse(any(name.endswith(tuple(local + [reference])) for name in names))
            for name in LOCAL_FILES:
                self.assertTrue((whale/name).is_file())
