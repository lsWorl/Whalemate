"""T23 图层自检的回归测试，使用微型合成图。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
try:
    from PIL import Image
except ImportError:
    Image = None
else:
    import check_q_layers as check


@unittest.skipIf(Image is None, "需要 Pillow")
class LayerReviewTest(unittest.TestCase):
    def test_green_white_and_transparent_samples(self):
        im = Image.new("RGBA", (4, 1))
        im.putdata(
            [
                (20, 60, 20, 180),
                (253, 251, 250, 100),
                (0, 255, 0, 0),
                (253, 251, 250, 255),
            ]
        )
        self.assertEqual(
            check.edge_counts(im), {"green_excess_12": 1, "near_white_translucent": 1}
        )

    def test_difference_includes_alpha_and_reports_worst_region(self):
        master = Image.new("RGB", (128, 64), check.GREEN)
        master.paste((255, 0, 0), (64, 0, 128, 64))
        result = Image.new("RGBA", master.size)
        result.paste((255, 0, 0, 255), (64, 0, 96, 64))
        result.putpixel((1, 1), (0, 0, 0, 255))
        stats, _, _ = check.difference(master, result)
        self.assertEqual(stats["master_pixels"], 4096)
        self.assertEqual(stats["outside_master"], 1)
        self.assertEqual(stats["worst_64px_box"], [64, 0, 128, 64])
        self.assertEqual(stats["mean_abs_rgb"], 85)

    def test_missing_layer_is_reported_before_rendering(self):
        with tempfile.TemporaryDirectory() as temp:
            pack = {"parts": {}, "review_layers": {}}
            with self.assertRaisesRegex(ValueError, "hair_back"):
                check.load_layers(Path(temp), pack)
