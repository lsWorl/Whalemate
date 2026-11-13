"""本机接口：只监听 127.0.0.1，带令牌。AstrBot 插件通过它把消息交给桌宠的大脑。

POST /v1/chat   {"platform","sender_id","sender_name","group_id","text"} → {"reply","silent","reason"}
GET  /v1/ping   → {"ok": true, "name": 角色名}
POST /v1/heartbeat {"astrbot","onebot":{"connected","self_id","error"},"groups":[{"id","name"}]}
                → {"ok": true, "groups": [允许的群号], "media": {...}, "outbox"?: [主动发到 QQ 私聊的消息]}
                  插件每 15 秒一次：桌宠据此显示连接状态，插件据此拿到允许的群和待发的主动消息
POST /v1/outbox/claim {"id","astrbot"} → {"allowed", "arrival_clock"?}（领取成功才发送）
POST /v1/outbox/ack {"id","status","sent","first_delay"?,"first_after_claim"?,"first_wall"?,"claim_round_trip"?} → {"ok"}
                  首次平台确认的墙钟时刻及单调耗时用于对齐首达时间；重试沿用同一首次确认。
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading

MAX_BODY = 5*1024*1024   # 最多 3 张、每张 1 MiB 的图片（base64 有约 1/3 额外体积）


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
                if self.path not in ('/v1/chat', '/v1/heartbeat', '/v1/media-permission','/v1/expression/claim','/v1/expression/ack','/v1/outbox/claim','/v1/outbox/ack'): return self._send(404, {'error': 'not_found'})
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    if length <= 0 or length > MAX_BODY: return self._send(413, {'error': 'too_large'})
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict): raise ValueError
                except ValueError:
                    return self._send(400, {'error': 'bad_json'})
                if self.path == '/v1/expression/claim': return self._send(200,server.service.expression_claim(payload))
                if self.path == '/v1/expression/ack': return self._send(200,server.service.expression_ack(payload))
                if self.path == '/v1/outbox/claim':
                    claim = getattr(server.service, 'outbox_claim', None)
                    return self._send(200, claim(payload) if claim else {'allowed': False})
                if self.path == '/v1/outbox/ack':
                    ack = getattr(server.service, 'outbox_ack', None)
                    return self._send(200, ack(payload) if ack else {'ok': False})
                if self.path == '/v1/heartbeat': return self._send(200, server.heartbeat(payload))
                if self.path == '/v1/media-permission': return self._send(200, server.service.media_permission(payload))
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
        beat = {'at': time.time(), 'astrbot': str(payload.get('astrbot') or '')[:20], 'groups': groups,
                'onebot': {'connected': bool(onebot.get('connected')), 'self_id': str(onebot.get('self_id') or '')[:20],
                           'error': str(onebot.get('error') or '')[:200]}}
        runtime.last_heartbeat = beat
        from aicompanion.bridge.service import setting
        take = getattr(self.service, 'take_outbox', None)
        outbox = take(beat) if take else []
        reply = {'ok': True, 'groups': [str(g) for g in setting(self.service.database, 'bridge_groups') or []],
                # 插件据此决定要不要下载别人发的图：识图关了或现在接的模型不收图片，就不传（只标一个中性的 [图片]）
                'media': {'vision': (self.service.vision_ready() if hasattr(self.service, 'vision_ready') else bool(self.service.database.get_setting('qq_vision_enabled', True))),
                          # 插件据此决定要不要把别人发的表情包原图附上来（关掉收集就不传图）
                          'collect': bool(self.service.database.get_setting('sticker_collect_enabled', True)),
                          'name': getattr(getattr(self.service.controller, 'character', None), 'name', '鲸鲸')}}
        if outbox:
            reply['outbox'] = [dict(item, require_plugin_version=True) for item in outbox]
        return reply

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
