"""为每次构建创建空目录，并让 wheel 和 sdist 使用同一份自绘美术清单。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.egg_info import egg_info, manifest_maker

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
from whale_art_files import whale_art_files

WHALE = Path('src/aicompanion/characters/deepseek-whale')
ART_FILES = [str(WHALE / relative) for relative in whale_art_files(WHALE)]


class BuildPython(build_py):
    def find_data_files(self, package, src_dir):
        files = super().find_data_files(package, src_dir)
        return [*files, *ART_FILES] if package == 'aicompanion' else files


class SourceManifest(manifest_maker):
    def read_template(self):
        # MANIFEST.in 先剔除旧 SOURCES.txt 缓存的整个美术目录，再加入正式资源。
        super().read_template()
        self.filelist.extend(ART_FILES)


class EggInfo(egg_info):
    def find_sources(self):
        manifest = SourceManifest(self.distribution)
        manifest.ignore_egg_info_dir = self.ignore_egg_info_in_manifest
        manifest.manifest = str(Path(self.egg_info) / 'SOURCES.txt')
        manifest.run()
        self.filelist = manifest.filelist


with TemporaryDirectory(prefix='whalemate-build-') as build_dir:
    setup(cmdclass={'build_py': BuildPython, 'egg_info': EggInfo},
          options={'build': {'build_base': build_dir}})
