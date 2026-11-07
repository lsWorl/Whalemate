"""形象包编译（T6）：占位素材 → AICompanionRig v1，不依赖 Qt 和网络；需要 Pillow。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import PIL  # noqa: F401
except ImportError:
    PIL = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))

ENGINE_DEFORMERS = {'body', 'waist', 'neck', 'armNear', 'armFar', 'legBack', 'legFront', 'tail', 'tailBend', 'skirt', 'skirtSit',
                    'hairSway', 'bangsSway', 'headBack', 'headMid', 'headFeat', 'headFront', 'finNear', 'finFar', 'ahoge'}


@unittest.skipIf(PIL is None, '需要 Pillow')
class RigPackTest(unittest.TestCase):
    def setUp(self):
        from make_placeholder_pack import make
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.art = make(self.root/'art', 512); self.out = self.root/'rig'

    def tearDown(self):
        self.tmp.cleanup()

    def compile(self):
        from aicompanion.body.rig_pack import compile_pack
        return compile_pack(self.art, self.out)

    def test_compiles_and_loads(self):
        from aicompanion.body.rig_import import load_rig
        report = self.compile()
        self.assertEqual(report.expressions, ['neutral', 'happy', 'love', 'surprised', 'sleep', 'drag', 'dizzy', 'shy', 'smug'])
        rig = load_rig(self.out/'rig.json')
        self.assertEqual((rig['format'], rig['version'], rig['source']['kind']), ('AICompanionRig', 1, 'pack'))
        self.assertEqual(set(rig['deformers']), ENGINE_DEFORMERS)
        self.assertEqual(rig['pivots']['body'], [128.0, 256.0])          # 中线和地面换算正确
        self.assertTrue(all(len(p['box']) == 4 and p['box'][2] > 0 for p in rig['parts']))
        self.assertEqual(sorted(rig['features']['eyes']['eyeL']), ['ball', 'hl', 'iris', 'lash'])
        self.assertIn('talk_mouth', rig['features']['sprites']); self.assertIn('shy_eyeL', rig['features']['sprites'])

    def test_crops_to_content(self):
        from PIL import Image
        self.compile()
        with Image.open(self.out/'textures'/'face.png') as face:
            self.assertLess(face.width, 512); self.assertLess(face.height, 512)

    def test_reports_all_problems(self):
        from PIL import Image
        from aicompanion.body.rig_pack import PackError
        (self.art/'layers'/'bangs.png').unlink()
        Image.new('RGBA', (100, 100)).save(self.art/'layers'/'body.png')
        pack = json.loads((self.art/'pack.json').read_text(encoding='utf-8'))
        del pack['pivots']['neck']; pack['pivots']['armNear'] = [10, 10]
        (self.art/'pack.json').write_text(json.dumps(pack), encoding='utf-8')
        with self.assertRaises(PackError) as caught: self.compile()
        text = '\n'.join(caught.exception.problems)
        for expected in ('layers/bangs.png', 'body.png 的尺寸', 'pivots.neck', '支点 armNear'):
            self.assertIn(expected, text)

    def test_missing_required_expression(self):
        from aicompanion.body.rig_pack import PackError
        (self.art/'layers'/'face'/'happy'/'mouth.png').unlink()
        with self.assertRaises(PackError) as caught: self.compile()
        self.assertIn('face/happy/mouth.png', '\n'.join(caught.exception.problems))

    def test_optional_expression_can_be_absent(self):
        import shutil
        shutil.rmtree(self.art/'layers'/'face'/'smug')
        self.assertNotIn('smug', self.compile().expressions)


if __name__ == '__main__':
    unittest.main()
