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
        # 两格均差同为 85，左侧格仅有一个额外轮廓像素，也必须参加统计。
        self.assertEqual(stats["worst_64px_box"], [0, 0, 64, 64])
        self.assertEqual(stats["mean_abs_rgb"], 85)
        self.assertEqual(
            {tuple(t["box"]) for t in stats["tiles_over_limit"]},
            {(0, 0, 64, 64), (64, 0, 128, 64)},
        )

    def test_small_islands_use_eight_neighbors_and_strict_area(self):
        alpha = Image.new("L", (32, 32))
        for i in range(16):
            alpha.putpixel((i, i), 1)
        alpha.paste(255, (20, 20, 25, 23))
        self.assertEqual(check.island_boxes(alpha), [[20, 20, 25, 23]])

    def test_tile_threshold_is_inclusive(self):
        master = Image.new("RGB", (64, 64), (0, 0, 0))
        composite = Image.new("RGBA", master.size, (40, 40, 40, 255))
        stats, _, _ = check.difference(master, composite)
        self.assertEqual(stats["tiles_over_limit"], [])
        composite.putpixel((0, 0), (41, 40, 40, 255))
        stats, _, _ = check.difference(master, composite)
        self.assertGreater(stats["tiles_over_limit"][0]["mean"], 40)

    def test_blink_compares_curve_not_only_bounding_boxes(self):
        with tempfile.TemporaryDirectory() as temp:
            art = Path(temp)
            root = art / "layers" / "face"
            for name in ("neutral", "sleep"):
                (root / name).mkdir(parents=True)
            for side in ("eyeL", "eyeR"):
                line = Image.new("RGBA", (20, 20))
                for x in range(2, 17):
                    line.putpixel((x, 3), (10, 10, 10, 255))
                line.save(root / "neutral" / (side + "_lash.png"))
                line.putpixel((8, 3), (0, 0, 0, 0))
                line.putpixel((8, 8), (10, 10, 10, 255))
                line.save(root / "sleep" / (side + ".png"))
            self.assertEqual(check.blink_errors(art), {"eyeL": 5, "eyeR": 5})

    def test_missing_layer_is_reported_before_rendering(self):
        with tempfile.TemporaryDirectory() as temp:
            pack = {"parts": {}, "review_layers": {}}
            with self.assertRaisesRegex(ValueError, "hair_back"):
                check.load_layers(Path(temp), pack)
