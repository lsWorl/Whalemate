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

    def test_iris_must_stay_inside_eye_white(self):
        from PIL import Image, ImageDraw
        from aicompanion.body.rig_pack import PackError
        iris = Image.new('RGBA', (512, 512)); ImageDraw.Draw(iris).ellipse((200, 200, 320, 300), fill=(0, 0, 255, 255))   # 比眼白还大
        iris.save(self.art/'layers'/'face'/'neutral'/'eyeL_iris.png')
        with self.assertRaises(PackError) as caught: self.compile()
        self.assertIn('eyeL_iris.png 要画在眼白以内', '\n'.join(caught.exception.problems))

    def test_missing_required_expression(self):
        from aicompanion.body.rig_pack import PackError
        (self.art/'layers'/'face'/'happy'/'mouth.png').unlink()
        with self.assertRaises(PackError) as caught: self.compile()
        self.assertIn('face/happy/mouth.png', '\n'.join(caught.exception.problems))

    def test_output_cannot_touch_sources(self):
        from aicompanion.body.rig_pack import PackError, compile_pack
        for out in (self.art, self.art/'layers', self.art/'build', self.root):     # 相同、在源目录里、源目录的祖先
            with self.assertRaises(PackError): compile_pack(self.art, out)
            self.assertTrue((self.art/'pack.json').exists() and (self.art/'layers'/'face.png').exists())

    def test_refuses_foreign_output_dir(self):
        from aicompanion.body.rig_pack import PackError, compile_pack
        foreign = self.root/'character'; foreign.mkdir(); (foreign/'character.json').write_text('{}')
        with self.assertRaises(PackError): compile_pack(self.art, foreign)
        self.assertTrue((foreign/'character.json').exists())
        other = self.root/'other-rig'; other.mkdir(); (other/'rig.json').write_text('{"format": "AICompanionRig", "source": {"kind": "coopanion"}}')
        with self.assertRaises(PackError): compile_pack(self.art, other)

    def test_failed_compile_keeps_previous_model(self):
        from aicompanion.body.rig_pack import PackError
        self.compile(); before = (self.out/'rig.json').read_text(encoding='utf-8')
        (self.art/'layers'/'face.png').unlink()
        with self.assertRaises(PackError): self.compile()
        self.assertEqual((self.out/'rig.json').read_text(encoding='utf-8'), before)
        self.assertTrue((self.out/'textures'/'face.png').exists())
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir() if p.name.startswith('.rig-')), [])   # 没留临时目录

    def test_recompile_replaces_only_owned_files(self):
        self.compile(); self.compile()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['rig.json', 'textures'])

    def test_bad_id_rejected(self):
        from aicompanion.body.rig_pack import PackError
        pack = json.loads((self.art/'pack.json').read_text(encoding='utf-8')); pack['id'] = '../../evil'
        (self.art/'pack.json').write_text(json.dumps(pack), encoding='utf-8')
        with self.assertRaises(PackError): self.compile()
        import rig_studio
        with self.assertRaises(PackError): rig_studio.default_out(self.art)

    def test_optional_expression_can_be_absent(self):
        import shutil
        shutil.rmtree(self.art/'layers'/'face'/'smug')
        self.assertNotIn('smug', self.compile().expressions)


if __name__ == '__main__':
    unittest.main()
