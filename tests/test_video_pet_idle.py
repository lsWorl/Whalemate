import unittest
from fractions import Fraction

from PIL import Image, ImageDraw

from tools.video_pet import batch, edges, idle, transition


class IdleProcessingTests(unittest.TestCase):
    def test_single_play_preserves_frame_order(self):
        frames = [Image.new("RGBA", (16, 16), color) for color in ("red", "blue", "white")]
        played, indices, _ = batch.plan_loop("once", frames, [])
        self.assertEqual(played, frames)
        self.assertEqual(indices, [0, 1, 2])

    def test_loop_compares_both_seams_and_skips_repeated_endpoints(self):
        values = [14, 15, 36, 52, 102, 141, 151, 170, 188, 200, 213, 215]
        frames = [Image.new("RGBA", (16, 16), (v, 0, 0, 255)) for v in values]
        played, order, scores = batch.plan_loop("loop", frames, [])
        self.assertLess(scores["reverse"], scores["fade"])
        self.assertEqual(order, list(range(12)) + list(range(10, 0, -1)))
        self.assertAlmostEqual(idle.compare(played[-1], played[0])[0], scores["reverse"])

    def test_gait_returns_to_same_foot_after_opposite_lift(self):
        height = [0, 16, 16, 16, 0, -16, -16, -16] * 3
        records = [{"feet": [(10, 50), (18, 50 + diff)]} for diff in height]
        frames = [Image.new("RGBA", (16, 16), (i % 8 * 4, 0, 0, 255)) for i in range(len(height))]
        _, order, loop = batch.plan_loop("gait", frames, records)
        self.assertEqual(len(order), 8)
        self.assertEqual(height[loop["start"]], height[loop["end"]])
        self.assertEqual({height[i] for i in order}, {0, 16, -16})

    def test_report_source_comes_from_prompts_record(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            clips = Path(root) / "clips"
            clips.mkdir()
            (Path(root) / "prompts.json").write_text(json.dumps({"videos": [
                {"id": "walk-v2", "platform": "即梦网页版", "model": "Seedance 2.0 mini", "mode": "首尾帧"}]},
                ensure_ascii=False), encoding="utf-8")
            self.assertEqual(batch.clip_source(clips / "walk-v2.mp4"), "即梦网页版 Seedance 2.0 mini，首尾帧")
            self.assertEqual(batch.clip_source(clips / "other.mp4"), "生成来源未在 prompts.json 中登记")

    def test_transition_keeps_endpoints_and_writes_reverse(self):
        import json, tempfile
        from pathlib import Path
        self.assertEqual(transition.pick(12, 30, 4), [12, 18, 24, 30])
        with tempfile.TemporaryDirectory() as root:
            pack = Path(root)
            w, h = idle.TILE
            sheet = Image.new("RGBA", (w * idle.COLUMNS, h * 4))
            sheet.save(pack / "turn.webp", lossless=True)
            (pack / "animations.json").write_text(json.dumps({"actions": {"idle": {}, "walk": {}}}), encoding="utf-8")
            forward, backward = transition.build(pack, pack / "turn.webp", 0, 20, 9, "idle", "walk", "转身")
            self.assertEqual((forward["from"], forward["to"], forward["frameCount"]), ("idle", "walk", 9))
            self.assertEqual(backward["frameMap"], list(range(8, -1, -1)))
            with self.assertRaises(ValueError):
                transition.build(pack, pack / "turn.webp", 0, 30, 37, "idle", "walk", "转身")

    def test_prop_action_uses_intro_loop_and_outro(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            pack = Path(root)
            w, h = idle.TILE
            Image.new("RGBA", (w * idle.COLUMNS, h * 8)).save(pack / "phone-src.webp", lossless=True)
            (pack / "animations.json").write_text(json.dumps({"actions": {"idle": {}}, "poses": {}}), encoding="utf-8")
            entry = transition.build_action(pack, pack / "phone-src.webp", 12, 15, "phone", "看手机", "phone")
            self.assertEqual(entry["frameMap"], [0, 1, 2, 3, 2, 1])
            once = transition.build_action(pack, pack / "phone-src.webp", 2, 9, "wave", "挥手", pingpong=False)
            self.assertEqual(once["frameCount"], 8)
            self.assertNotIn("frameMap", once)
            intro = transition.build_one_way(pack, pack / "phone-src.webp", 0, 12, 13, "idle", "phone", "掏手机")
            outro = transition.build_one_way(pack, pack / "phone-src.webp", 48, 60, 13, "phone", "idle", "收手机")
            manifest = json.loads((pack / "animations.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["poses"]["phone"], "phone")
            self.assertEqual([(t["from"], t["to"]) for t in manifest["transitions"]], [("idle", "phone"), ("phone", "idle")])
            self.assertNotIn("frameMap", intro); self.assertNotIn("frameMap", outro)
            again = transition.build_one_way(pack, pack / "phone-src.webp", 0, 12, 13, "idle", "phone", "掏手机")
            self.assertEqual(again["image"], intro["image"])
            self.assertEqual(len(list(pack.glob("transition-*.webp"))), 2)

    def test_stamp_uses_each_corners_own_background(self):
        rgb = Image.new("RGB", (320, 192), (149, 246, 32))
        self.assertEqual(batch.corner_mark(rgb), [])
        ImageDraw.Draw(rgb).rectangle((291, 173, 300, 177), fill="white")
        self.assertEqual(batch.corner_mark(rgb), [(291, 173, 301, 178)])

    def test_sampling_uses_timestamps_and_excludes_returned_anchor(self):
        times = list(map(Fraction, ("0", ".05", ".08333333", ".16", ".25")))
        self.assertEqual(idle.playback_indices(times), [0, 2, 3])

    def test_motion_of_hair_does_not_move_shoes(self):
        base = Image.new("RGBA", (120, 160))
        draw = ImageDraw.Draw(base)
        for x in (30, 63):
            draw.rectangle((x, 115, x + 15, 144), fill="navy")
        draw.ellipse((12, 10, 85, 90), fill="blue")
        windows = idle.shoe_windows(base)
        original = idle.foot_point(base, windows)
        moved = idle.translate(base, 3, -2)
        ImageDraw.Draw(moved).ellipse((0, 0, 28, 18), fill="white")
        measured = idle.foot_point(moved, windows)
        self.assertEqual(tuple(a - b for a, b in zip(measured, original)), (3, -2))

    def test_key_keeps_opaque_face_and_clears_dark_green_screen(self):
        image = Image.new("RGB", (320, 120), (0, 181, 0))
        ImageDraw.Draw(image).rectangle((100, 12, 150, 111), fill=(247, 226, 217))
        frame = idle.chroma_key(image)
        self.assertEqual(frame.getpixel((10, 10))[3], 0)
        self.assertEqual(frame.getpixel((125, 42)), (247, 226, 217, 255))
        self.assertEqual(idle.green_count(idle.tile_frame(frame)), 0)

    def test_soft_edge_borrows_nearest_solid_color_even_across_thin_tip(self):
        rgb = Image.new("RGB", (21, 18), "black")
        alpha = Image.new("L", rgb.size)
        for point, color, opacity in [((2, 8), (241, 221, 201), 250),
                                      ((18, 6), (55, 44, 77), 255)]:
            rgb.putpixel(point, color)
            alpha.putpixel(point, opacity)
        alpha.putpixel((10, 6), 100)
        alpha.putpixel((4, 8), 61)
        keyed = edges.inner_borrow(rgb, alpha)
        self.assertEqual(keyed.getpixel((10, 6)), (55, 44, 77, 100))
        self.assertEqual(keyed.getpixel((4, 8)), (241, 221, 201, 61))

    def test_local_background_follows_gradient_across_subject(self):
        source = Image.new("RGB", (640, 192))
        draw = ImageDraw.Draw(source)
        for x in range(source.width):
            draw.line((x, 0, x, 191), fill=(2, 120 + x // 5, 4))
        draw.rectangle((285, 65, 350, 120), fill=(213, 190, 181))
        field = edges.background(source)
        self.assertGreater(field.getpixel((576, 90))[1] - field.getpixel((64, 90))[1], 80)
        self.assertAlmostEqual(field.getpixel((320, 90))[1], 184, delta=5)

    def test_feather_has_cutoff_and_partial_coverage(self):
        alpha = Image.new("L", (60, 50))
        ImageDraw.Draw(alpha).rectangle((10, 10, 49, 39), fill=255)
        softened = edges.feather(alpha)
        bins = softened.histogram()
        self.assertEqual(sum(bins[1:26]), 0)
        self.assertGreater(sum(bins[26:250]), 0)
        self.assertLess(softened.getpixel((10, 24)), 128)
