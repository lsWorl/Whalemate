import unittest

import numpy as np

from tools.video_pet import dehand


class DehandTests(unittest.TestCase):
    def test_only_skin_touching_top_edge_is_removed(self):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        frame[:] = (10, 160, 20)
        skin = (240, 200, 170)
        frame[0:40, 60:80] = skin      # 从上缘伸进来的手臂
        frame[70:90, 30:50] = skin     # 角色自己的脸，不碰上缘
        region = dehand.hand_region(frame)
        self.assertTrue(region[10, 70])
        self.assertTrue(region[44, 70])   # 外扩盖住描线
        self.assertFalse(region[80, 40])
        self.assertFalse(region[100, 70])

    def test_no_hand_returns_empty_mask(self):
        frame = np.zeros((60, 80, 3), dtype=np.uint8)
        frame[:] = (10, 160, 20)
        frame[30:50, 30:50] = (240, 200, 170)
        self.assertFalse(dehand.hand_region(frame).any())


if __name__ == "__main__":
    unittest.main()
