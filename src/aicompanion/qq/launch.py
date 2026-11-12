"""AstrBot 的启动命令：用虚拟环境背后的基础解释器直接运行，不经过虚拟环境里的 exe。

为什么（2026-11-09 真机诊断，Windows 智能应用控制 Smart App Control 为"开启"）：
- uv 创建虚拟环境时，`Scripts\\astrbot.exe`（uv 的启动器）和 `Scripts\\python.exe`（复制出来的 venv 启动器）都没有签名、
  也没有足够信誉，`python.exe` 被代码完整性策略拦截（事件 3033 / 3077），`astrbot.exe` 拉起它时报
  `uv trampoline failed to spawn Python child process (os error 4551)`。
- 同一套 uv 装的基础解释器 `python\\cpython-3.12…\\python.exe` 不被拦截；用它加上虚拟环境的 site-packages
  按 console script 入口运行 `astrbot run`，AstrBot 能完整启动（WebUI、内置插件），期间没有新的拦截事件。

所以不改 Windows 安全设置、不关闭智能应用控制，只换启动方式。启动参数：
- `-I`：隔离模式，不读 PYTHONPATH / 用户 site-packages，桌宠或用户环境里的 Python 包不会混进来；
- `-X utf8`：`-I` 会忽略 PYTHONUTF8 / PYTHONIOENCODING，这里显式开 UTF-8，否则 AstrBot 的日志在中文 Windows 上按 GBK 写会报编码错误；
- 用 `site.addsitedir` 加入虚拟环境的 site-packages（会处理其中的 .pth），入口从已安装的包元数据读取，不写死模块路径。

找不到基础解释器（例如 pyvenv.cfg 缺失、指向组件目录以外）时退回原来的 `Scripts\\astrbot.exe`，行为和以前一样。
"""
from __future__ import annotations

from pathlib import Path

# 在基础解释器里执行：argv = ['-c', 虚拟环境目录, AstrBot 参数...]
BOOT = (
    "import os,site,sys\n"
    "from importlib.metadata import distribution\n"
    "venv=sys.argv[1]\n"
    "site.addsitedir(os.path.join(venv,'Lib','site-packages'))\n"
    "entry=next(e for e in distribution('astrbot').entry_points if e.group=='console_scripts' and e.name=='astrbot')\n"
    "sys.argv=['astrbot']+sys.argv[2:]\n"
    "sys.exit(entry.load()())\n"
)


def base_python(env_dir: Path, owner) -> Path | None:
    """虚拟环境 pyvenv.cfg 里 home 指向的基础解释器；只认组件目录（owner.owns）里的、确实存在的 python.exe。"""
    try:
        lines = (Path(env_dir)/'pyvenv.cfg').read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        key, sep, value = line.partition('=')
        if sep and key.strip().lower() == 'home':
            python = Path(value.strip())/'python.exe'
            if python.is_file() and owner.owns(python): return python
            return None
    return None


def astrbot_command(env_dir: Path, owner, *args: str) -> list:
    env_dir = Path(env_dir)
    python = base_python(env_dir, owner)
    if python is None:
        return [env_dir/'Scripts'/'astrbot.exe', *args]
    return [python, '-I', '-X', 'utf8', '-c', BOOT, env_dir, *args]
