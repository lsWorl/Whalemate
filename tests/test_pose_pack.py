"""十姿态分层包（T6 第 A 节）：合成一个迷你 pose-layered-v2 → AICompanionPoseRig v1，不依赖 Qt 和网络；需要 Pillow。"""
import json
from pathlib import Path
import tempfile
import unittest

try:
    import PIL  # noqa: F401
except ImportError:
    PIL = None

CANVAS = 200
ANCHOR = [100, 190]
# 每层：id、z、父节点、box（源像素）、颜色
PARTS = [('edge_residual', 0, 'root', [60, 20, 80, 10], (10, 10, 10)), ('hair_back', 1, 'hair', [50, 30, 100, 110], (40, 40, 160)),
         ('tail', 2, 'tail', [130, 120, 40, 20], (60, 120, 220)), ('leg_left', 3, 'leg_left', [80, 150, 15, 40], (240, 240, 240)),
         ('torso', 4, 'body', [70, 100, 60, 60], (30, 30, 60)), ('arm_left', 7, 'arm_left', [55, 105, 20, 40], (30, 30, 70)),
         ('head_skin', 8, 'head', [70, 40, 60, 60], (250, 220, 200)), ('hair_front', 9, 'head', [65, 35, 70, 30], (50, 50, 180)),
         ('fin_left', 11, 'head', [45, 50, 25, 15], (200, 230, 255)), ('ahoge', 12, 'head', [95, 15, 12, 20], (50, 50, 180)),
         ('eye_left', 13, 'head', [80, 65, 12, 10], (20, 90, 220)), ('eye_right', 13.1, 'head', [108, 65, 12, 10], (20, 90, 220)),
         ('mouth', 14, 'head', [95, 85, 10, 5], (180, 60, 60))]
NODES = {'root': None, 'body': 'root', 'head': 'body', 'hair': 'body', 'arm_left': 'body', 'tail': 'body', 'leg_left': 'body'}
POSES = ('idle', 'talking', 'thinking', 'working', 'celebrate', 'error', 'wait', 'sleepy', 'grabbed', 'poked')


def make_source(root: Path, broken: str = '') -> Path:
    """按 pose-layered-v2 的结构写迷你源包：poses/<姿态>/model.json + cropped/ + 拼好的 originals/。"""
    from PIL import Image
    root.mkdir(parents=True, exist_ok=True)
    (root/'originals').mkdir()
    index = {}
    for pose in POSES:
        folder = root/'poses'/pose/'cropped'; folder.mkdir(parents=True)
        full = Image.new('RGBA', (CANVAS, CANVAS), (0, 0, 0, 0))
        parts = []
        for pid, z, parent, box, color in PARTS:
            x, y, w, h = box
            Image.new('RGBA', (w, h), color + (255,)).save(folder/f'{pid}.png')
            full.alpha_composite(Image.new('RGBA', (w, h), color + (255,)), (x, y))
            parts.append({'id': pid, 'file': f'poses/{pose}/layers/{pid}.png', 'crop_file': f'poses/{pose}/cropped/{pid}.png',
                          'box': box, 'parent': parent, 'z': z})
        full.save(root/'originals'/f'{pose}.png')
        anchor = [110, 60] if pose == 'grabbed' else ANCHOR
        model = {'id': pose, 'canvas': [CANVAS, CANVAS], 'source': f'originals/{pose}.png', 'anchor': anchor, 'airborne': pose == 'grabbed',
                 'nodes': {n: {'parent': p, 'pivot': [100, 100] if n == 'head' else ANCHOR} for n, p in NODES.items()}, 'parts': parts, 'variants': {}}
        if pose == 'talking':
            # 闭嘴替换：嘴换成一条线；顺带让 tail 有标注支点，验证 motion_ready
            Image.new('RGBA', (10, 2), (120, 40, 40, 255)).save(folder/'mouth_mouth_closed.png')
            closed = full.copy(); closed.paste(Image.new('RGBA', (10, 5), (0, 0, 0, 0)), (95, 85))
            closed.paste(Image.new('RGBA', (10, 5), (250, 220, 200, 255)), (95, 85))
            closed.alpha_composite(Image.new('RGBA', (10, 2), (120, 40, 40, 255)), (95, 86))
            closed.save(root/'originals'/'talking_mouth_closed.png')
            Image.new('RGBA', (10, 5), (250, 220, 200, 255)).save(folder/'mouth_base.png')
            model['variants']['mouth_closed'] = {
                'source': 'originals/talking_mouth_closed.png',
                'overrides': {'mouth': f'poses/{pose}/layers/mouth_mouth_closed.png'},
                'parts': [{'id': 'mouth_mouth_closed', 'crop_file': f'poses/{pose}/cropped/mouth_mouth_closed.png', 'box': [95, 86, 10, 2], 'parent': 'head', 'z': 14}]}
            model['nodes']['tail'] = {'parent': 'body', 'pivot': [130, 130]}
            model['motion_ready'] = ['tail']
        if pose == broken: parts.pop()   # 少一层 → 回拼不一致；mouth 是必需层 → 检查失败
        (root/'poses'/pose/'model.json').write_text(json.dumps(model), encoding='utf-8')
        index[pose] = f'poses/{pose}/model.json'
    (root/'model.json').write_text(json.dumps({'version': 'pose-layered-v2', 'canvas': [CANVAS, CANVAS], 'poses': index}), encoding='utf-8')
    return root


@unittest.skipIf(PIL is None, '需要 Pillow')
class PosePackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_compiles_verifies_and_loads(self):
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_import import load_rig
        art = make_source(self.root/'art'); out = self.root/'rig'
        report = compile_posepack(art, out, verify=True)
        self.assertEqual(report.poses, list(POSES))
        self.assertEqual(report.parts, len(POSES)*len(PARTS)); self.assertEqual(report.variants, 1)
        self.assertTrue(all(v == 0 for v in report.verified.values()), report.verified)
        self.assertIn('talking/mouth_closed', report.verified)
        rig = load_rig(out/'rig.json')
        self.assertEqual((rig['format'], rig['version'], rig['source']['kind']), ('AICompanionPoseRig', 1, 'posepack'))
        idle = rig['poses']['idle']
        # anchor → (128, 256)，0.2 倍；grabbed 的挂点 → space.grab
        self.assertEqual(idle['nodes']['body']['pivot'], [128, 256])
        self.assertEqual(rig['poses']['grabbed']['nodes']['body']['pivot'], [128+(100-110)*0.2, rig['space']['grab'][1]+(190-60)*0.2])
        torso = next(p for p in idle['parts'] if p['id'] == 'torso')
        self.assertEqual(torso['rect'], [128-6, 256-18, 128+6, 256-6])
        # 替换层指向被替换的图层
        layer = rig['poses']['talking']['variants']['mouth_closed'][0]
        self.assertEqual((layer['replaces'], layer['node']), ('mouth', 'head'))
        # 没标注支点的鳍/呆毛/尾巴：不摆；有标注的 talking/tail 摆
        self.assertIsNone(next(p for p in idle['parts'] if p['id'] == 'fin_left')['swing'])
        self.assertEqual(next(p for p in rig['poses']['talking']['parts'] if p['id'] == 'tail')['swing'], 'tail')
        self.assertIn('idle/tail', report.disabled); self.assertNotIn('talking/tail', report.disabled)
        # 渲染器只让 swing_nodes 里的节点摆：未就绪的 tail 不在里面（节点矩阵只随身体）
        self.assertEqual(idle['swing_nodes'], []); self.assertEqual(rig['poses']['talking']['swing_nodes'], ['tail'])
        # edge_residual 只随身体；手臂和腿没有摆动
        self.assertEqual(next(p for p in idle['parts'] if p['id'] == 'edge_residual')['node'], 'body')
        self.assertTrue(all(p['swing'] is None for p in idle['parts'] if p['id'].startswith(('arm_', 'leg_'))))
        # 幅度不超过上限；贴图缩小一半
        for pose in rig['poses'].values():
            for key, limit in rig['limits'].items(): self.assertLessEqual(pose['motion'][key], limit)
        from PIL import Image
        with Image.open(out/torso['tex']) as image: self.assertEqual(image.size, (30, 30))

    def test_missing_part_and_mismatch(self):
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_pack import PackError
        art = make_source(self.root/'art', broken='poked')
        with self.assertRaises(PackError) as caught: compile_posepack(art, self.root/'rig')
        self.assertIn('poked 缺少图层 mouth', str(caught.exception))
        self.assertFalse((self.root/'rig').exists())

    def test_verify_detects_difference(self):
        from PIL import Image
        from aicompanion.body.pose_pack import compile_posepack
        art = make_source(self.root/'art')
        Image.new('RGBA', (5, 5), (255, 0, 0, 255)).save(art/'poses'/'idle'/'cropped'/'mouth.png')   # 改掉一层
        from aicompanion.body.pose_pack import read_source, check, verify_composites
        poses = check(art, read_source(art))[0]
        result = verify_composites(art, read_source(art), poses)
        self.assertGreater(result['idle'], 0); self.assertEqual(result['talking'], 0)

    def test_failed_verify_keeps_previous_generation(self):
        """--verify 不通过时不发布：旧输出整代（rig.json + 全部贴图）哈希不变。"""
        import hashlib
        from PIL import Image
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_pack import PackError
        art = make_source(self.root/'art'); out = self.root/'rig'
        compile_posepack(art, out, verify=True)
        digest = lambda: {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
        before = digest()
        Image.new('RGBA', (5, 5), (255, 0, 0, 255)).save(art/'poses'/'idle'/'cropped'/'mouth.png')
        with self.assertRaises(PackError) as caught: compile_posepack(art, out, verify=True)
        self.assertIn('idle 有', str(caught.exception)); self.assertEqual(digest(), before)
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith('.rig-')], [])

    def test_verify_fails_when_reference_missing(self):
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_pack import PackError
        art = make_source(self.root/'art')
        model = json.loads((art/'poses'/'idle'/'model.json').read_text(encoding='utf-8')); model['source'] = 'originals/nope.png'
        (art/'poses'/'idle'/'model.json').write_text(json.dumps(model), encoding='utf-8')
        with self.assertRaises(PackError) as caught: compile_posepack(art, self.root/'rig', verify=True)
        self.assertIn('idle 没有可比对的原图', str(caught.exception)); self.assertFalse((self.root/'rig').exists())

    def test_verify_counts_alpha_one_differences(self):
        """原图里 alpha=1 的像素在回拼里没有：Alpha 整幅比较，必须查出来。"""
        from PIL import Image
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_pack import PackError
        art = make_source(self.root/'art')
        with Image.open(art/'originals'/'idle.png') as image:
            image = image.convert('RGBA'); image.putpixel((2, 2), (0, 0, 0, 1)); image.putpixel((3, 2), (9, 9, 9, 1)); image.save(art/'originals'/'idle.png')
        with self.assertRaises(PackError) as caught: compile_posepack(art, self.root/'rig', verify=True)
        self.assertIn('idle 有 2 个像素', str(caught.exception))

    def test_missing_pose_and_output_guard(self):
        import shutil
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_pack import PackError
        art = make_source(self.root/'art'); shutil.rmtree(art/'poses'/'wait')
        with self.assertRaises(PackError) as caught: compile_posepack(art, self.root/'rig')
        self.assertIn('缺少姿态 wait', str(caught.exception))
        with self.assertRaises(PackError): compile_posepack(make_source(self.root/'art2'), self.root/'art2'/'out')

    def test_runtime_check_reports_missing_texture(self):
        from aicompanion.body.pose_pack import compile_posepack
        from aicompanion.body.rig_import import RigImportError, load_rig
        out = self.root/'rig'; compile_posepack(make_source(self.root/'art'), out)
        rig = json.loads((out/'rig.json').read_text(encoding='utf-8'))
        (out/rig['poses']['sleepy']['parts'][0]['tex']).unlink()
        with self.assertRaises(RigImportError): load_rig(out/'rig.json')


if __name__ == '__main__':
    unittest.main()
