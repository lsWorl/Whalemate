import unittest
from types import SimpleNamespace

from aicompanion.ui.desktop_pet import walk_pace


def clip(distance, fps, count):
    return SimpleNamespace(cycle_distance=distance, fps=fps, count=count)


class WalkPaceTests(unittest.TestCase):
    def test_pace_follows_pack_walk_cycle(self):
        # 鲸鲸视频包：12 帧一个周期、12 fps、每周期 109 px，即每秒 109 px。
        pack = SimpleNamespace(poses={'walking': 'walk'}, actions={'walk': clip(109., 12, 12)})
        self.assertAlmostEqual(walk_pace(pack), 109.)

    def test_aboutme_style_pack_prefers_right_walk(self):
        pack = SimpleNamespace(poses={'walking': 'walking_right', 'walking_right': 'walking_right'},
                               actions={'walking_right': clip(80 / 12 * 40, 12, 80)})
        self.assertAlmostEqual(walk_pace(pack), 40.)

    def test_default_without_walk_clip(self):
        self.assertEqual(walk_pace(None), 42.)
        self.assertEqual(walk_pace(SimpleNamespace(poses={}, actions={})), 42.)


if __name__ == '__main__':
    unittest.main()
