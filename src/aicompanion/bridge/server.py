"""本机接口：只监听 127.0.0.1，带令牌。AstrBot 插件通过它把消息交给桌宠的大脑。

POST /v1/chat   {"platform","sender_id","sender_name","group_id","text"} → {"reply","silent","reason"}
GET  /v1/ping   → {"ok": true, "name": 角色名}
POST /v1/heartbeat {"astrbot","onebot":{"connected","self_id","error"},"groups":[{"id","name"}]}
                → {"ok": true, "groups": [允许的群号]}   插件每 15 秒一次：桌宠据此显示连接状态，插件据此拿到允许的群
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading

MAX_BODY = 64*1024


class BridgeServer:
    def __init__(self, service, port: int, token: str, host: str = '127.0.0.1'):
        self.service, self.token = service, token
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def _send(self, code, data):
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(code); self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

            def _authorized(self) -> bool:
                given = self.headers.get('Authorization', '')
                return bool(server.token) and hmac.compare_digest(given.encode(), f'Bearer {server.token}'.encode())

            def do_GET(self):
                if not self._authorized(): return self._send(401, {'error': 'unauthorized'})
                if self.path != '/v1/ping': return self._send(404, {'error': 'not_found'})
                self._send(200, {'ok': True, 'name': server.service.controller.character.name})

            def do_POST(self):
                if not self._authorized(): return self._send(401, {'error': 'unauthorized'})
                if self.path not in ('/v1/chat', '/v1/heartbeat'): return self._send(404, {'error': 'not_found'})
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if length <= 0 or length > MAX_BODY: return self._send(413, {'error': 'too_large'})
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict): raise ValueError
                except ValueError:
                    return self._send(400, {'error': 'bad_json'})
                if self.path == '/v1/heartbeat': return self._send(200, server.heartbeat(payload))
                self._send(200, server.service.handle(payload))

        self.httpd = ThreadingHTTPServer((host, port), Handler); self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name='aicompanion-bridge')
        self.thread.start()

    def heartbeat(self, payload: dict) -> dict:
        """记下插件心跳（只留状态和群列表，不留别的），回给插件当前允许的群。"""
        import re, time
        onebot = payload.get('onebot') if isinstance(payload.get('onebot'), dict) else {}
        groups = []
        for item in (payload.get('groups') or [])[:500]:
            if isinstance(item, dict) and re.fullmatch(r'\d{5,12}', str(item.get('id', ''))):
                groups.append({'id': str(item['id']), 'name': str(item.get('name') or '')[:40]})
        runtime.last_heartbeat = {'at': time.time(), 'astrbot': str(payload.get('astrbot') or '')[:20], 'groups': groups,
                                  'onebot': {'connected': bool(onebot.get('connected')), 'self_id': str(onebot.get('self_id') or '')[:20],
                                             'error': str(onebot.get('error') or '')[:200]}}
        from aicompanion.bridge.service import setting
        return {'ok': True, 'groups': [str(g) for g in setting(self.service.database, 'bridge_groups') or []]}

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def close(self):
        close = getattr(self.service, 'close', None)   # 还在排队的旧请求拿到锁后直接作废；旁听不再落库
        if close: close()
        else: self.service.closed = True
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=5)


def new_token() -> str:
    return secrets.token_urlsafe(24)


class Runtime:
    """按设置启动 / 重启 / 关闭接口；应用启动时和设置页保存时调用 apply。"""
    def __init__(self):
        self.server = None; self.database = None; self.controller = None; self.problem = ''
        self.last_heartbeat = None   # 插件最近一次心跳（内存里，不落库）

    def apply(self, database=None, controller=None) -> str:
        from aicompanion.bridge.service import BridgeService, setting
        self.database = database or self.database; self.controller = controller or self.controller
        self.close()
        if self.database is None or not setting(self.database, 'bridge_enabled'): self.problem = ''; return ''
        if self.controller is None: self.problem = '重启应用后接口才会打开。'; return self.problem
        token = setting(self.database, 'bridge_token')
        if not token:
            token = new_token(); self.database.set_setting('bridge_token', token)
        try:
            self.server = BridgeServer(BridgeService(self.database, self.controller), int(setting(self.database, 'bridge_port')), token)
            self.problem = ''
        except OSError as error:
            self.problem = f'聊天平台接口没能启动（端口 {setting(self.database, "bridge_port")}）：{error}'
        return self.problem

    def close(self):
        if self.server: self.server.close(); self.server = None


runtime = Runtime()
