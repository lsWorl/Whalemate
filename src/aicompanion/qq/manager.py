"""一键接入 QQ 的总控：准备组件、写配置、启动 / 停止、诊断。不依赖 Qt；界面在后台线程里调用这些方法。

设置都存在桌宠数据库的 app_settings 里（只填一次）：
  qq_mode           '' / 'auto'（自动安装并管理）/ 'existing'（接入已有 AstrBot）
  qq_root           组件目录（空 = %LOCALAPPDATA%\\AICompanion\\qq）
  qq_existing_root  已有 AstrBot 的根目录（existing 模式）
  qq_ports          {'onebot', 'webui', 'dashboard'}
  qq_secrets        {'onebot', 'webui', 'dashboard'}：三段各自的随机令牌 / 密码，不写日志
  qq_bot_uin        机器人 QQ 号（登录成功后记下，下次用 -q 快速登录）
  qq_autostart      打开桌宠时自动启动组件
  qq_index_url      PyPI 镜像（高级设置，空 = 官方）
插件 ↔ 桌宠的令牌沿用第一版的 bridge_token，端口 bridge_port。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time

from aicompanion.qq import configure, specs
from aicompanion.qq.fetch import fetch
from aicompanion.qq.layout import Layout, default_root, install_archive
from aicompanion.qq.napcat_webui import NapCatWebUI, WebUIError
from aicompanion.qq.supervisor import Supervisor

DEFAULT_PORTS = {'onebot': 6199, 'webui': 6099, 'dashboard': 6185}
HEARTBEAT_STALE = 45


class SetupError(RuntimeError):
    pass


@dataclass
class Status:
    code: str          # not_setup / not_ready / bridge_off / stopped / start_failed / starting / need_login / offline /
                       # plugin_missing / onebot_down / need_owner / connected
    title: str         # 给普通用户看的一句话
    action: str = ''   # 下一步按钮 / 提示
    detail: str = ''   # 诊断细节（高级）
    qrcode: str = ''   # 需要扫码时二维码图片路径
    groups: list = field(default_factory=list)   # 机器人所在的群（来自插件心跳）
    bot: str = ''      # 已登录的机器人 QQ 号


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try: probe.bind(('127.0.0.1', int(port))); return True
        except OSError: return False


def pick_port(preferred: int, taken=(), free=port_free) -> int:
    """优先用习惯端口；被占用就往后找，再不行让系统分配。"""
    for port in range(int(preferred), int(preferred)+50):
        if port not in taken and free(port): return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0)); return probe.getsockname()[1]


def child_env(extra: dict) -> dict:
    """组件的环境变量：去掉桌宠自己的模型密钥，不让上游程序拿到。"""
    env = {k: v for k, v in os.environ.items() if not (k.upper().endswith('_API_KEY') or k.upper() in ('LLM_API_KEY', 'QWEN_API_KEY'))}
    env.update({k: str(v) for k, v in extra.items()}); return env


class QQManager:
    def __init__(self, database, heartbeat=None, run=subprocess.run, supervisor=None, fetcher=fetch, free=port_free,
                 webui_factory=NapCatWebUI, clock=time.time):
        self.db = database
        self.heartbeat = heartbeat or (lambda: None)   # () → 最近一次插件心跳 dict 或 None
        self.run, self.fetcher, self.free, self.webui_factory, self.clock = run, fetcher, free, webui_factory, clock
        self.layout = Layout(self.db.get_setting('qq_root', '') or default_root())
        self.supervisor = supervisor or Supervisor(self.layout)
        self._webui = None
        self.last_error = ''

    # ---- 设置 ----------------------------------------------------------------------------
    @property
    def mode(self) -> str: return self.db.get_setting('qq_mode', '') or ''

    def secrets(self) -> dict:
        values = dict(self.db.get_setting('qq_secrets', {}) or {})
        changed = False
        for key in ('onebot', 'webui', 'dashboard'):
            if not values.get(key): values[key] = secrets.token_urlsafe(24); changed = True
        if changed: self.db.set_setting('qq_secrets', values)
        return values

    def ports(self, reassign: bool = False) -> dict:
        """端口：没定过就挑空闲的；reassign 时把被别的程序占了的换掉（我们自己的进程在跑时它占着是正常的）。"""
        ports = dict(self.db.get_setting('qq_ports', {}) or {})
        ours_running = self.supervisor.running('napcat') or self.supervisor.running('astrbot')
        changed = False
        for key, preferred in DEFAULT_PORTS.items():
            current = ports.get(key)
            if not current or (reassign and not ours_running and not self.free(current)):
                ports[key] = pick_port(preferred, taken=[v for k, v in ports.items() if k != key], free=self.free); changed = True
        if changed: self.db.set_setting('qq_ports', ports)
        return ports

    def bridge(self) -> tuple[str, str]:
        from aicompanion.bridge.server import new_token
        from aicompanion.bridge.service import setting
        token = setting(self.db, 'bridge_token')
        if not token: token = new_token(); self.db.set_setting('bridge_token', token)
        return f'http://127.0.0.1:{int(setting(self.db, "bridge_port"))}', token

    def known_secrets(self) -> list:
        return [*self.secrets().values(), self.bridge()[1]]

    # ---- 准备组件（下载、安装、配置；可重试，失败不破坏已有版本） ----------------------------
    def prepare(self, progress=lambda step, text, fraction=0.0: None, cancelled=lambda: False) -> None:
        if os.name != 'nt' and not getattr(self, 'allow_non_windows', False):
            raise SetupError('自动安装目前只支持 Windows x64')
        self.db.set_setting('qq_mode', 'auto')
        layout = self.layout; layout.root.mkdir(parents=True, exist_ok=True)
        components = dict(layout.manifest().get('components') or {})
        for index, spec in enumerate((specs.NAPCAT, specs.UV)):
            step = f'下载 {spec.name}'
            archive = self.fetcher(spec.url, layout.downloads/Path(spec.url).name, spec.sha256,
                                   progress=lambda done, total, s=step: progress(s, f'{done/1e6:.0f} / {total/1e6:.0f} MB' if total else f'{done/1e6:.0f} MB', done/total if total else 0),
                                   cancelled=cancelled)
            progress(f'安装 {spec.name}', '解压并检查文件')
            path = install_archive(layout, spec, archive)
            components[spec.name] = {'version': spec.version, 'url': spec.url, 'sha256': spec.sha256, 'path': str(path)}
            layout.update_manifest(components=components)
        self._install_astrbot(components, progress, cancelled)
        self.configure(progress)

    def _uv(self, *args, timeout=1800):
        uv = self.layout.current('uv')/'uv.exe'
        env = child_env({'UV_PYTHON_INSTALL_DIR': self.layout.python_dir, 'UV_CACHE_DIR': self.layout.uv_cache,
                         'UV_NO_MODIFY_PATH': '1', 'UV_PYTHON_PREFERENCE': 'only-managed'})
        index = self.db.get_setting('qq_index_url', '') or ''
        if index: env['UV_DEFAULT_INDEX'] = index
        result = self.run([str(uv), *map(str, args)], env=env, capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0:
            raise SetupError(f'uv {args[0]} 失败：' + (result.stderr or result.stdout or '').strip()[-600:])
        return result.stdout

    def _install_astrbot(self, components, progress, cancelled):
        spec = specs.ASTRBOT
        current = components.get('astrbot') or {}
        # 只复用"按当前固定哈希实际校验过"的同版本环境；上一版装的（没有校验标记）或哈希变了的，在新目录里重新受控安装
        verified = current.get('wheel_verified') == 'uv --require-hashes' and current.get('wheel_sha256') == spec.wheel_sha256
        if (current.get('version') == spec.version and verified and self.layout.current('astrbot')
                and (Path(current['path'])/'Scripts'/'astrbot.exe').is_file()):
            return
        progress('安装 Python', f'Python {spec.python}（uv 自动下载并校验）')
        self._uv('python', 'install', spec.python)
        if cancelled(): raise SetupError('已取消')
        target = self.layout.root/'astrbot'/f'{spec.version}-{secrets.token_hex(3)}'   # 新目录：失败时旧环境不动
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            progress('安装 AstrBot', '创建独立运行环境')
            self._uv('venv', target, '--python', spec.python)
            python = target/'Scripts'/'python.exe'
            # 第一步：只装 AstrBot 本身，强制按 specs 里固定的 wheel 哈希核对（不管用的是哪个索引 / 镜像）
            progress('安装 AstrBot', f'{spec.name} {spec.version}（核对固定的文件哈希）')
            pinned = target/'aicompanion-pinned.txt'
            pinned.write_text(f'{spec.name}=={spec.version} --hash=sha256:{spec.wheel_sha256}\n', encoding='utf-8')
            self._uv('pip', 'install', '--python', python, '--no-deps', '--require-hashes', '-r', pinned)
            # 第二步：补齐依赖（AstrBot 已装、版本相同，不会被换掉）；依赖的完整性由 uv 按索引公布的哈希检查，装完记录 freeze
            progress('安装 AstrBot', '依赖（较慢，请稍候）')
            self._uv('pip', 'install', '--python', python, f'{spec.name}=={spec.version}')
            freeze = self._uv('pip', 'freeze', '--python', python)
            if f'{spec.name.lower()}=={spec.version}' not in freeze.lower():
                raise SetupError('AstrBot 装完后版本不对')
        except Exception:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            raise
        old = current.get('path')
        components['astrbot'] = {'version': spec.version, 'wheel_sha256': spec.wheel_sha256, 'wheel_verified': 'uv --require-hashes',
                                 'python': spec.python, 'path': str(target), 'freeze': freeze.splitlines()}
        self.layout.update_manifest(components=components)
        if old and old != str(target) and self.layout.owns(Path(old)) and not self.supervisor.running('astrbot'):
            import shutil
            shutil.rmtree(old, ignore_errors=True)   # 新环境已可用，清掉我们自己的旧环境

    def configure(self, progress=lambda *a, **k: None) -> None:
        """写三处配置并安装插件；端口被占用时换端口。只改我们自己的那一项。"""
        progress('写配置', '连接 NapCat、AstrBot 和桌宠')
        ports, keys = self.ports(reassign=True), self.secrets()
        url, token = self.bridge()
        layout = self.layout
        configure.napcat_config(layout.napcat_home, ports['webui'], keys['webui'], f'ws://127.0.0.1:{ports["onebot"]}/ws', keys['onebot'])
        configure.astrbot_init(layout.astrbot_home)
        configure.astrbot_config(layout.astrbot_home, ports['onebot'], keys['onebot'], ports['dashboard'])
        configure.install_plugin(layout.astrbot_home, configure.plugin_source())
        configure.plugin_config(layout.astrbot_home, url, token)
        self.ensure_bridge()

    def ensure_bridge(self) -> None:
        """接入 / 启动 / 重试是用户明确要用 QQ：打开桌宠接口并让它真的跑起来（没开就 apply）。
        用户之后在高级设置里关掉接口，那是他的选择；状态会显示"接口没有打开"，点重试再打开。"""
        from aicompanion.bridge.server import runtime
        self.db.set_setting('bridge_enabled', True)
        if runtime.server is None:
            problem = runtime.apply(self.db)
            if problem: raise SetupError(problem)

    # ---- 接入已有实例 ---------------------------------------------------------------------
    def attach_existing(self, astrbot_root) -> Path:
        """只装 / 更新我们的插件和它的配置文件；不改 cmd_config.json、其他插件、NapCat 和账号数据。"""
        root = Path(astrbot_root)
        if not (root/'data').is_dir(): raise SetupError('这个文件夹不是 AstrBot 的根目录（里面应该有 data 文件夹）')
        target = configure.install_plugin(root, configure.plugin_source())
        url, token = self.bridge()
        configure.plugin_config(root, url, token)
        self.db.set_setting('qq_mode', 'existing'); self.db.set_setting('qq_existing_root', str(root))
        self.ensure_bridge()
        return target

    # ---- 启动 / 停止 ------------------------------------------------------------------------
    def ready(self) -> list[str]:
        """缺了什么（空 = 组件都在）。"""
        missing = []
        if not self.layout.current('napcat'): missing.append('NapCat')
        env = self.layout.current('astrbot')
        if not env or not (env/'Scripts'/'astrbot.exe').is_file(): missing.append('AstrBot')
        if not configure.is_our_plugin(self.layout.astrbot_home/'data'/'plugins'/specs.PLUGIN_NAME): missing.append('插件')
        return missing

    def start(self) -> None:
        # 配置写入、创建进程都可能在 Popen 返回前失败；这时没有退出码或新日志。
        # 保留异常供定时检查和“复制诊断信息”使用，不能下一轮只剩 stopped。
        self.last_error = ''
        try:
            self._start()
        except Exception as error:
            message = f'QQ 服务启动失败（{type(error).__name__}）：{error}'
            raise SetupError(self.remember_error(message)) from error

    def remember_error(self, error) -> str:
        """启动及页面上的准备 / 同步操作共用：保留脱敏原因，让后续状态刷新仍可诊断。"""
        message = str(error)
        for secret in self.known_secrets():
            if secret: message = message.replace(secret, '***')
        self.last_error = message
        return message

    def _start(self) -> None:
        if self.mode != 'auto': raise SetupError('只有"自动安装并管理"模式由桌宠启动组件')
        missing = self.ready()
        if missing: raise SetupError('组件还没准备好：'+'、'.join(missing))
        self.configure()   # 每次启动前同步一次配置（端口冲突、令牌、插件更新）
        keys, layout = self.secrets(), self.layout
        astrbot = layout.current('astrbot')
        self.supervisor.start('astrbot', [astrbot/'Scripts'/'astrbot.exe', 'run'], layout.astrbot_home,
                              child_env({'ASTRBOT_ROOT': layout.astrbot_home, 'ASTRBOT_DASHBOARD_INITIAL_PASSWORD': keys['dashboard'],
                                         'ASTRBOT_DISABLE_METRICS': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}))
        napcat = layout.current('napcat'); uin = str(self.db.get_setting('qq_bot_uin', '') or '')
        layout.qq_data.mkdir(parents=True, exist_ok=True)
        self.supervisor.start('napcat', [napcat/'node.exe', 'index.js', *(['-q', uin] if uin else [])], napcat,
                              child_env({'NAPCAT_WORKDIR': layout.napcat_home, 'NAPCAT_QQ_DATA_DIR': layout.qq_data}))
        self._webui = None

    def stop(self) -> None:
        for name in ('napcat', 'astrbot'): self.supervisor.stop(name)
        self.last_error = ''

    # ---- 诊断 ---------------------------------------------------------------------------------
    def webui(self):
        if self._webui is None:
            self._webui = self.webui_factory(self.ports()['webui'], self.secrets()['webui'])
        return self._webui

    def owner_bound(self) -> bool:
        return bool(str(self.db.get_setting('bridge_owner:qq', '') or '').strip())

    def diagnose(self) -> Status:
        from aicompanion.bridge.server import runtime
        mode = self.mode
        if mode not in ('auto', 'existing'):
            return Status('not_setup', '还没接入 QQ', '接入 QQ', self.last_error)
        if mode == 'auto':
            missing = self.ready()
            if missing:
                detail = '\n'.join(filter(None, ('缺少：'+'、'.join(missing), self.last_error)))
                return Status('not_ready', '组件还没准备好', '准备组件', detail)
        if not runtime.server:
            detail = '\n'.join(filter(None, (runtime.problem, self.last_error)))
            return Status('bridge_off', '桌宠的接口没有打开', '重试', detail)
        beat = self.heartbeat(); fresh = bool(beat) and self.clock()-beat.get('at', 0) < HEARTBEAT_STALE
        groups = list((beat or {}).get('groups') or [])
        if mode == 'auto':
            if self.last_error:
                return Status('start_failed', 'QQ 服务没能启动', '重试', self.last_error)
            for name, label in (('astrbot', 'AstrBot'), ('napcat', 'NapCat')):
                code = self.supervisor.exited_with(name)
                if code is not None:
                    detail = self.supervisor.tail(name, secrets=self.known_secrets())
                    if 'os error 4551' in detail or 'WinError 4551' in detail:
                        detail = ('Windows 应用控制阻止了组件运行（4551），不是 QQ 登录或插件连接错误。'
                                  '请在 Windows 安全中心及代码完整性事件中确认被阻止的文件，使用符合系统信任要求的运行环境；'
                                  '重复启动或重置账号配置不能解除此拦截。\n'+detail)
                        return Status('start_failed', f'{label} 被 Windows 应用控制阻止', '重试', detail)
                    return Status('start_failed', f'{label} 启动后退出了（代码 {code}）', '重试', detail)
                if not self.supervisor.running(name):
                    return Status('stopped', 'QQ 服务没有在运行', '启动')
            try:
                state = self.webui().login_status()
            except WebUIError as error:
                return Status('starting', '正在启动，请稍等…', '', str(error))
            if not state.get('isLogin'):
                qrcode = self.layout.napcat_home/'cache'/'qrcode.png'
                if state.get('isOffline'):
                    return Status('offline', '机器人账号掉线了，请重新扫码登录', '刷新二维码', state.get('loginError') or '',
                                  str(qrcode) if qrcode.is_file() else '')
                return Status('need_login', '请用手机 QQ 扫码登录机器人账号', '刷新二维码', state.get('loginError') or '',
                              str(qrcode) if qrcode.is_file() else '')
            self._remember_bot()
        if not fresh:
            if mode == 'existing':
                return Status('plugin_missing', '还没收到 AstrBot 插件的消息', '查看步骤',
                              '请在 AstrBot 里重载插件 astrbot_plugin_aicompanion，并确认 AstrBot 正在运行', groups=groups)
            return Status('plugin_missing', 'AstrBot 插件还没连上桌宠', '重试', self.supervisor.tail('astrbot', secrets=self.known_secrets()), groups=groups)
        onebot = beat.get('onebot') or {}
        if not onebot.get('connected'):
            hint = '' if mode == 'auto' else '请检查 NapCat 的 OneBot 反向 WebSocket 是否指向 AstrBot'
            return Status('onebot_down', 'QQ 登录了，但还没连上 AstrBot', '重试', hint or str(onebot.get('error') or ''), groups=groups)
        bot = str(onebot.get('self_id') or '')
        if not self.owner_bound():
            return Status('need_owner', '已连接。请填写你自己的 QQ 号（她管你叫爹）', '保存', groups=groups, bot=bot)
        return Status('connected', '已连接', '', groups=groups, bot=bot)

    def _remember_bot(self):
        try: uin = str(self.webui().login_info().get('uin') or '')
        except WebUIError: return
        if uin and uin != str(self.db.get_setting('qq_bot_uin', '') or ''): self.db.set_setting('qq_bot_uin', uin)

    def refresh_qrcode(self):
        try: self.webui().refresh_qrcode()
        except WebUIError: pass

    def diagnostics_text(self) -> str:
        """复制给别人看的诊断信息：版本、端口、状态；令牌和密码一律不出现。"""
        status = self.diagnose(); manifest = self.layout.manifest()
        lines = [f'状态：{status.code} {status.title}', f'模式：{self.mode or "未设置"}', f'组件目录：{self.layout.root}']
        for name, entry in (manifest.get('components') or {}).items():
            lines.append(f'{name}：{entry.get("version")} sha256={str(entry.get("sha256") or entry.get("wheel_sha256") or "")[:12]}…')
        lines.append(f'端口：{self.db.get_setting("qq_ports", {})}')
        beat = self.heartbeat() or {}
        version = str(beat.get('astrbot') or '')
        if re.fullmatch(r'plugin-[0-9]+(?:\.[0-9]+){1,3}', version):
            freshness = '最近心跳' if self.clock()-beat.get('at', 0) < HEARTBEAT_STALE else '心跳已过期'
            lines.append(f'AstrBot 桥接插件：{version}（{freshness}；不是磁盘文件版本）')
        else:
            lines.append('AstrBot 桥接插件：尚未收到有效版本心跳')
        if status.detail: lines += ['细节：', status.detail]
        text = '\n'.join(lines)
        for secret in self.known_secrets(): text = text.replace(secret, '***')
        return text
