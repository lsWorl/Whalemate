"""WebEngine 用来画桌宠和（可选）收音播放：在创建 QApplication 之前设置一组保守的 Chromium 参数，降低常驻内存。

用户自己设置了 QTWEBENGINE_CHROMIUM_FLAGS 时不覆盖。
"""
import os

FLAGS = [
    '--renderer-process-limit=1',            # 只开一个渲染进程
    '--js-flags=--max-old-space-size=96',    # 限制 JS 堆
    '--disable-background-networking',       # 不做后台联网
    '--disable-gpu-shader-disk-cache',       # 不写着色器磁盘缓存
    '--disk-cache-size=1',
    '--disable-extensions',
    '--disable-features=Translate,OptimizationHints,MediaRouter,SpareRendererForSitePerProcess',
    # P4 语音网页：不可见页面也要按时收音和播放；回声消除以浏览器内所有播放为参考
    '--autoplay-policy=no-user-gesture-required',
    '--disable-background-timer-throttling',
    '--disable-renderer-backgrounding',
    '--enable-features=ChromeWideEchoCancellation',
]


def apply_webengine_flags() -> str:
    return os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', ' '.join(FLAGS))
