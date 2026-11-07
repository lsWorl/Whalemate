"""生成 / 更新 NapCat、AstrBot、插件的配置。只改我们自己那一项（按名字 / id 识别），其他内容原样保留。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile

from aicompanion.qq.specs import ONEBOT_CLIENT_NAME, PLATFORM_ID, PLUGIN_NAME

MARKER = '.aicompanion-managed'   # 插件目录里的标记：有它才说明是我们装的


class ConfigError(RuntimeError):
    pass


def read_json(path: Path, default):
    # AstrBot 4.29.3 保存主配置和插件配置时使用 utf-8-sig；首次运行后就会带 BOM。
    # utf-8-sig 同时接受有 / 无 BOM 的 UTF-8，重启时仍能保留用户已有配置。
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError: return default
    except ValueError as error: raise ConfigError(f'{path} 不是有效的 JSON，没有改动它：{error}') from error


def write_json(path: Path, data) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle: json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp, path)


# ---- NapCat ---------------------------------------------------------------------------
def napcat_config(home: Path, webui_port: int, webui_token: str, onebot_url: str, onebot_token: str) -> None:
    """NAPCAT_WORKDIR/config 下：webui.json（只监听本机）、onebot11.json（新账号的模板）和已登录账号的 onebot11_<号>.json。"""
    config = Path(home)/'config'
    webui = read_json(config/'webui.json', {})
    webui.update({'host': '127.0.0.1', 'port': int(webui_port), 'token': webui_token})
    write_json(config/'webui.json', webui)
    targets = [config/'onebot11.json', *sorted(config.glob('onebot11_*.json'))]
    for path in targets:
        data = read_json(path, {})
        network = data.setdefault('network', {})
        for key in ('httpServers', 'httpSseServers', 'httpClients', 'websocketServers', 'websocketClients', 'plugins'):
            network.setdefault(key, [])
        clients = [c for c in network['websocketClients'] if c.get('name') != ONEBOT_CLIENT_NAME]
        clients.append({'name': ONEBOT_CLIENT_NAME, 'enable': True, 'url': onebot_url, 'messagePostFormat': 'array',
                        'reportSelfMessage': False, 'reconnectInterval': 5000, 'token': onebot_token, 'debug': False,
                        'heartInterval': 30000})
        network['websocketClients'] = clients
        write_json(path, data)


# ---- AstrBot -----------------------------------------------------------------------------
def astrbot_init(root: Path) -> None:
    """和 `astrbot init` 做同样的事：.astrbot 标记 + data/、data/config、data/plugins、data/temp。"""
    root = Path(root)
    for sub in ('data', 'data/config', 'data/plugins', 'data/temp'): (root/sub).mkdir(parents=True, exist_ok=True)
    (root/'.astrbot').touch(exist_ok=True)


def astrbot_config(root: Path, ws_port: int, ws_token: str, dashboard_port: int) -> None:
    """data/cmd_config.json：加 / 更新我们的 aiocqhttp 平台（id=aicompanion_qq），面板只监听本机。缺的键 AstrBot 启动时自己补默认值。"""
    path = Path(root)/'data'/'cmd_config.json'
    conf = read_json(path, {})
    platforms = [p for p in conf.get('platform', []) if p.get('id') != PLATFORM_ID]
    platforms.append({'id': PLATFORM_ID, 'type': 'aiocqhttp', 'enable': True, 'ws_reverse_host': '127.0.0.1',
                      'ws_reverse_port': int(ws_port), 'ws_reverse_token': ws_token})
    conf['platform'] = platforms
    dashboard = conf.setdefault('dashboard', {})
    dashboard.update({'host': '127.0.0.1', 'port': int(dashboard_port)})
    write_json(path, conf)


# ---- 插件 ------------------------------------------------------------------------------------
def install_plugin(astrbot_root: Path, source: Path) -> Path:
    """把项目里的插件复制到 data/plugins/astrbot_plugin_aicompanion。目标已存在但不是我们装的（没有标记、
    metadata 名字不对）就拒绝；先复制到临时目录，成功后再替换，失败时旧插件保持原样。"""
    plugins = Path(astrbot_root)/'data'/'plugins'
    if not plugins.is_dir(): raise ConfigError(f'{astrbot_root} 不像 AstrBot 的根目录（没有 data/plugins）')
    target = plugins/PLUGIN_NAME
    if target.exists() and not is_our_plugin(target):
        raise ConfigError(f'{target} 已存在且不是桌宠安装的，没有覆盖；请先自己移走它')
    # 临时目录和备份目录都是本次用 mkdtemp 新建、名字唯一的；只清理这两个，plugins 里预先存在的任何目录都不碰
    staging = Path(tempfile.mkdtemp(prefix=f'.{PLUGIN_NAME}-new-', dir=plugins))
    backup_dir = Path(tempfile.mkdtemp(prefix=f'.{PLUGIN_NAME}-old-', dir=plugins)); backup = backup_dir/PLUGIN_NAME
    built = staging/PLUGIN_NAME
    moved = False
    try:
        built.mkdir()
        for item in Path(source).iterdir():
            if item.name in ('__pycache__',) or item.name.startswith('.'): continue
            (shutil.copytree if item.is_dir() else shutil.copy2)(item, built/item.name)
        (built/MARKER).write_text('由 AICompanion 桌宠安装和更新；手动修改会在下次更新时被覆盖。\n', encoding='utf-8')
        if target.exists():
            target.rename(backup); moved = True
        built.rename(target)
        return target
    except Exception:
        if moved and backup.exists() and not target.exists(): backup.rename(target)   # 发布失败：旧插件放回原处
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if not (moved and backup.exists() and not target.exists()):   # 旧插件已放回或已被新版本替换，才清理本次的备份目录
            shutil.rmtree(backup_dir, ignore_errors=True)


def is_our_plugin(path: Path) -> bool:
    path = Path(path)
    if not (path/MARKER).is_file(): return False
    try: meta = (path/'metadata.yaml').read_text(encoding='utf-8')
    except OSError: return False
    return any(line.strip() == f'name: {PLUGIN_NAME}' for line in meta.splitlines())


def plugin_config(astrbot_root: Path, pet_url: str, token: str) -> None:
    """data/config/astrbot_plugin_aicompanion_config.json：设地址和令牌，用户改过的其他项（离线提示等）保留。
    group_only_when_called 统一关掉：旧版插件默认开、AstrBot 会把默认值写进这个文件，不改的话升级后她还是只能被 @ 才说话；
    群里插不插话现在由桌宠的"群里活跃度"决定（选"只在被叫时说话"就是旧行为）。"""
    path = Path(astrbot_root)/'data'/'config'/f'{PLUGIN_NAME}_config.json'
    conf = read_json(path, {})
    conf.update({'pet_url': pet_url, 'token': token, 'group_only_when_called': False})
    write_json(path, conf)


def plugin_source() -> Path:
    """项目里的插件源码（开发时在仓库 integrations/ 下；打包后随程序放在同名目录）。"""
    here = Path(__file__).resolve()
    for base in (here.parents[3]/'integrations', here.parents[1]/'integrations'):
        if (base/PLUGIN_NAME/'main.py').is_file(): return base/PLUGIN_NAME
    raise ConfigError('找不到随桌宠附带的 AstrBot 插件')
