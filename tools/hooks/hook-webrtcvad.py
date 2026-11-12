"""webrtcvad-wheels 提供同名模块，但发行包名称不同于旧 webrtcvad。"""
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata('webrtcvad-wheels')
hiddenimports = ['_webrtcvad']
