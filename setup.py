"""为本次分发使用空的构建目录，避免旧 build/lib 的本机贴图混入 wheel。"""
from tempfile import TemporaryDirectory

from setuptools import setup


with TemporaryDirectory(prefix='whalemate-build-') as build_dir:
    setup(options={'build': {'build_base': build_dir}})
