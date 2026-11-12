"""NapCat WebUI 后端的几个接口（源码 packages/napcat-webui-backend）：登录令牌 → Credential，查 QQ 登录状态、刷新二维码。"""
from __future__ import annotations

import hashlib
import json
import urllib.request


class WebUIError(RuntimeError):
    pass


class NapCatWebUI:
    def __init__(self, port: int, token: str, opener=urllib.request.urlopen, host='127.0.0.1'):
        self.base = f'http://{host}:{int(port)}/api'; self.token = token; self.opener = opener; self.credential = None

    def _post(self, path, body=None, auth=True, retry=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            if not self.credential: self.login()
            headers['Authorization'] = f'Bearer {self.credential}'
        request = urllib.request.Request(self.base+path, data=json.dumps(body or {}).encode(), headers=headers, method='POST')
        try:
            with self.opener(request, timeout=5) as response: data = json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            if error.code in (401, 403) and auth and retry:   # Credential 过期：重新换一次
                self.credential = None; return self._post(path, body, auth, retry=False)
            raise WebUIError(f'NapCat 面板返回 {error.code}') from error
        except (OSError, ValueError) as error:
            raise WebUIError(f'连不上 NapCat 面板：{error}') from error
        if data.get('code') not in (0, None):
            raise WebUIError(str(data.get('message') or data))
        return data.get('data')

    def login(self):
        digest = hashlib.sha256((self.token+'.napcat').encode()).hexdigest()
        data = self._post('/auth/login', {'hash': digest}, auth=False)
        if not data or not data.get('Credential'): raise WebUIError('NapCat 面板令牌不对')
        self.credential = data['Credential']

    def login_status(self) -> dict:
        """{isLogin, isOffline, qrcodeurl, loginError, ...}"""
        return self._post('/QQLogin/CheckLoginStatus') or {}

    def login_info(self) -> dict:
        return self._post('/QQLogin/GetQQLoginInfo') or {}

    def refresh_qrcode(self) -> None:
        self._post('/QQLogin/RefreshQRcode')
