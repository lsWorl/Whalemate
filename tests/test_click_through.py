"""T27：鼠标穿透只改 WS_EX_TRANSPARENT 一位，不碰分层样式；非分层窗口交回 Qt。用假的 user32，不需要 Windows。"""
import ctypes
from ctypes import wintypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aicompanion.ui import desktop_integration as desktop


class FakeUser32:
    def __init__(self, style):
        self.style, self.writes = style, []

    def GetWindowLongPtrW(self, hwnd, index):
        return self.style

    def SetWindowLongPtrW(self, hwnd, index, value):
        self.writes.append(value); self.style = value


class ClickThroughTests(unittest.TestCase):
    def run_with(self, style, enabled):
        fake = FakeUser32(style)
        widget = SimpleNamespace(winId=lambda: 1234)
        with patch.object(desktop, 'IS_WINDOWS', True), patch.object(desktop, 'user32', fake, create=True), \
                patch.object(desktop, 'wintypes', wintypes, create=True):
            handled = desktop.set_click_through(widget, enabled)
        return handled, fake

    def test_toggles_only_the_transparent_bit_on_layered_windows(self):
        layered = desktop.WS_EX_LAYERED | 0x8   # 0x8 = WS_EX_TOPMOST，必须保留
        handled, fake = self.run_with(layered, True)
        self.assertTrue(handled)
        self.assertEqual(fake.writes, [layered | desktop.WS_EX_TRANSPARENT])
        handled, fake = self.run_with(layered | desktop.WS_EX_TRANSPARENT, False)
        self.assertEqual(fake.writes, [layered])

    def test_no_write_when_already_in_state(self):
        handled, fake = self.run_with(desktop.WS_EX_LAYERED | desktop.WS_EX_TRANSPARENT, True)
        self.assertTrue(handled)
        self.assertEqual(fake.writes, [])

    def test_non_layered_window_falls_back_to_qt(self):
        handled, fake = self.run_with(0x8, True)
        self.assertFalse(handled)
        self.assertEqual(fake.writes, [])

    def test_other_platforms_fall_back_to_qt(self):
        with patch.object(desktop, 'IS_WINDOWS', False):
            self.assertFalse(desktop.set_click_through(SimpleNamespace(winId=lambda: 1), True))


if __name__ == '__main__':
    unittest.main()
