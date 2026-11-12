"""T5 交付校验应检查透明通道的细小变化与替换引用，不能只看预览。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HAS_PIL = importlib.util.find_spec('PIL') is not None


@unittest.skipUnless(HAS_PIL, '源素材回拼检查需要 Pillow')
class PoseSourceVerificationTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location('t5_pose_verifier', Path(__file__).resolve().parents[1]/'tools/verify_pose_sources.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.verify = module.verify
        index = {'format': 'AICompanionPosePack', 'version': 1, 'canvas': [8, 8], 'poses': {}, 'source_sha256': {}}
        names = 'idle talking thinking working celebrate error wait sleepy grabbed poked'.split()
        for pose in names:
            folder = self.root/'poses'/pose/'cropped'
            folder.mkdir(parents=True)
            layer = Image.new('RGBA', (3, 3))
            layer.putpixel((1, 1), (40, 80, 160, 255))
            layer.save(folder/'part.png')
            original = Image.new('RGBA', (8, 8))
            original.alpha_composite(layer, (2, 2))
            source = self.root/'originals'/f'{pose}.png'
            source.parent.mkdir(exist_ok=True)
            original.save(source)
            file = f'poses/{pose}/cropped/part.png'
            model = {'canvas': [8, 8], 'source': f'originals/{pose}.png', 'nodes': {'root': {'parent': None, 'pivot': [4, 7]}},
                     'parts': [{'id': 'part', 'parent': 'root', 'file': file, 'crop_file': file, 'box': [2, 2, 3, 3], 'z': 1}], 'variants': {}}
            (folder.parent/'model.json').write_text(json.dumps(model), encoding='utf-8')
            index['poses'][pose] = model
            index['source_sha256'][source.name] = hashlib.sha256(source.read_bytes()).hexdigest()
        (self.root/'model.json').write_text(json.dumps(index), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_reconstruction(self):
        report = self.verify(self.root)
        self.assertTrue(report['ok'])
        self.assertEqual((report['poses'], report['reference_states']), (10, 10))

    def test_detects_alpha_one_difference(self):
        from PIL import Image
        path = self.root/'poses/idle/cropped/part.png'
        with Image.open(path) as image:
            image.putpixel((0, 0), (0, 0, 0, 1))
            image.save(path)
        report = self.verify(self.root)
        self.assertFalse(report['ok'])
        idle = next(c for c in report['checks'] if c['state'] == 'idle')
        self.assertEqual(idle['changed_alpha_pixels'], 1)

    def test_rejects_unmatched_variant_reference(self):
        path = self.root/'poses/idle/model.json'
        model = json.loads(path.read_text(encoding='utf-8'))
        model['variants']['eyes_closed'] = {'source': model['source'], 'overrides': {'part': 'missing.png'}, 'parts': []}
        path.write_text(json.dumps(model), encoding='utf-8')
        index = json.loads((self.root/'model.json').read_text(encoding='utf-8'))
        index['poses']['idle'] = model
        (self.root/'model.json').write_text(json.dumps(index), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '替换清单与路径不一致'):
            self.verify(self.root)
