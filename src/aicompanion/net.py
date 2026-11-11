"""共用的 HTTP 打开方式：所有带密钥的请求都不跟随重定向。

为什么：urllib 默认会跟随 301/302/303，并把 Authorization 头原样带到新地址。
如果配置的地址被跳转到别的域名，密钥就会发给不相干的服务。项目里原来有三份相同的
NoRedirect，这里合成一份，聊天、语音、搜索、社区抓取都用它。遇到跳转直接按 HTTP 错误处理，
由调用方提示用户检查地址。
"""
from __future__ import annotations

from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    """拒绝跟随任何重定向；urllib 会把 3xx 当成 HTTPError 抛给调用方。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(NoRedirect())


def open_url(request, timeout=None):
    """等价于 urlopen，但不跟随重定向。"""
    return _OPENER.open(request, timeout=timeout)
