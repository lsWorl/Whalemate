"""固定版本的上游组件：来源、SHA-256、解压后必须存在的文件。升级版本时只改这里，并在 T9 说明里记录依据。"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class Archive:
    name: str                 # 目录名：<根>/<name>/<目录>
    version: str
    url: str
    sha256: str
    size_mb: int
    required: tuple = ()      # 解压后必须存在的相对路径（校验包是不是对的）
    license_note: str = ''


@dataclass(frozen=True)
class PyPackage:
    name: str
    version: str
    wheel_sha256: str          # 该版本 wheel 的 SHA-256：安装时用 --require-hashes 强制核对（换镜像也一样），对不上就失败
    python: str = '3.12'
    extra_index: tuple = field(default_factory=tuple)


# NapCat 官方自包含 Windows 包：NapCat.Shell + QQ 运行所需文件 + node.exe，不需要预装 QQ、不注入系统 QQ
NAPCAT = Archive(
    'napcat', '4.18.41',
    'https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.41/NapCat.Shell.Windows.Node.zip',
    '51fbb10e7bcd56b0c2a6de86c37811e7518330e48a137ac689ab1e4d8627d3f0', 118,
    required=('node.exe', 'index.js', 'wrapper.node', 'napcat/napcat.mjs'),
    license_note='NapCat 由 NapNeko 开发，使用其"Limited Redistribution License"：仅限非商业用途。'
                 '桌宠从 NapCat 官方 GitHub 发布页下载原版文件，不修改、不再分发。',
)
# uv：在自己的目录里装 Python 3.12 和 AstrBot 的独立虚拟环境（AstrBot 官方脚本部署同样用 uv + Python 3.12）
UV = Archive(
    'uv', '0.13.5',
    'https://github.com/astral-sh/uv/releases/download/0.13.5/uv-x86_64-pc-windows-msvc.zip',
    'a38b8d1902b7cfbc76b423ba1bb456f623f42064cdc72628b6075b89afd1019a', 20,
    required=('uv.exe',),
    license_note='uv（Astral，MIT / Apache-2.0）。',
)
ASTRBOT = PyPackage('AstrBot', '4.29.3', 'b78d8ab2d6f153c18be7162799b8589c9f5284233d557c93364c22193fe6be8e')

PLUGIN_NAME = 'astrbot_plugin_aicompanion'
MIN_PROACTIVE_PLUGIN = (0, 10, 2)
PLATFORM_ID = 'aicompanion_qq'          # 我们在 AstrBot 里加的那个 aiocqhttp 平台
ONEBOT_CLIENT_NAME = 'aicompanion'      # 我们在 NapCat 里加的那条 websocketClient


def plugin_version(reported) -> tuple[int, ...]:
    """只认插件实际运行时报告的三段版本号，缺失或格式未知时无法确认投递能力。"""
    match = re.fullmatch(r'plugin-([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})', str(reported or ''))
    return tuple(map(int, match.groups())) if match else ()
