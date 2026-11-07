"""P1 分层骨骼：导入器、模型校验、命中图与遮罩。导入测试只在本机有 Coopanion 时运行。"""
import json
import tempfile
import unittest
from pathlib import Path

from aicompanion.body.rig_import import RigImportError, ensure_whale_rig, find_whale, load_rig

ROOT = Path(__file__).resolve().parents[1]
WHALE = find_whale(ROOT)


@unittest.skipIf(WHALE is None, '本机没有 Coopanion，跳过大肥鱼导入测试')
class WhaleImportTests(unittest.TestCase):
    def test_import_is_complete_and_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            path = ensure_whale_rig(ROOT, root=Path(temp))
            rig = load_rig(path)
            self.assertEqual(len(rig['parts']), 26)
            self.assertIn('happy_eyeL', rig['features']['sprites'])
            self.assertEqual(set(rig['features']['eyes']), {'eyeL', 'eyeR'})
            for part in rig['parts']:
                self.assertIn(part['deformer'], rig['deformers'])
            stamp = (path.parent/'.source').read_text(encoding='utf-8')
            self.assertEqual(ensure_whale_rig(ROOT, root=Path(temp)), path)
            self.assertEqual((path.parent/'.source').read_text(encoding='utf-8'), stamp)
            self.assertIn('不随 AICompanion 分发', rig['source']['notice'])

    def test_scheme_changes_textures(self):
        with tempfile.TemporaryDirectory() as temp:
            base = ensure_whale_rig(ROOT, root=Path(temp))
            claude = ensure_whale_rig(ROOT, scheme='claude', root=Path(temp))
            self.assertNotEqual(base.parent, claude.parent)
            a = (base.parent/'textures/skirt.png').read_bytes(); b = (claude.parent/'textures/skirt.png').read_bytes()
            self.assertNotEqual(a, b)

    def test_cache_is_outside_repository(self):
        from aicompanion.body.rig_import import cache_root
        self.assertFalse(str(cache_root().resolve()).startswith(str(ROOT.resolve())))


class RigValidationTests(unittest.TestCase):
    def test_bad_rig_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'rig.json'
            path.write_text(json.dumps({'format': 'other', 'version': 1}), encoding='utf-8')
            with self.assertRaises(RigImportError): load_rig(path)
            path.write_text(json.dumps({'format': 'AICompanionRig', 'version': 1, 'deformers': {},
                                        'parts': [{'id': 'x', 'deformer': 'missing', 'tex': 'x.png'}]}), encoding='utf-8')
            with self.assertRaises(RigImportError): load_rig(path)

    def test_missing_coopanion_returns_none(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(ensure_whale_rig(Path(temp), root=Path(temp)/'cache'))


class HitmapTests(unittest.TestCase):
    def test_parse_dilate_and_region(self):
        from aicompanion.body.rig_view import dilate, parse_hitmap, region_from_hitmap
        bits = '0000' '0110' '0000'
        data = parse_hitmap(json.dumps({'cell': 4, 'width': 4, 'height': 3, 'bits': bits, 'head': [8, 2], 'feet': [8, 12]}))
        self.assertIsNotNone(data)
        self.assertIsNone(parse_hitmap(json.dumps({'cell': 4, 'width': 4, 'height': 3, 'bits': '01'})))
        self.assertIsNone(parse_hitmap('not json'))
        rows = dilate(bits, 4, 3, 1)
        self.assertEqual([list(r) for r in rows], [[1, 1, 1, 1]]*3)
        region = region_from_hitmap(data, radius=0)
        self.assertTrue(region.contains(__import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(6, 6)))
        self.assertFalse(region.contains(__import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(1, 1)))


if __name__ == '__main__': unittest.main()
