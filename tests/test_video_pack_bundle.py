"""鲸鲸默认形象改为随程序发布的视频逐帧动画。"""
import unittest

from aicompanion.characters import PACKS_DIR, choose_renderer, load_pack
from aicompanion.characters.animation_pack import load_animation_pack
from aicompanion.pet.state import PRIORITY
from tools.whale_art_files import whale_art_files

WHALE = PACKS_DIR/'deepseek-whale'


class VideoPackBundleTests(unittest.TestCase):
    def test_default_renderer_uses_bundled_video_pack(self):
        pack = load_pack(WHALE)
        kind, manifest = choose_renderer(pack, None, '')
        self.assertEqual(kind, 'spritesheet')
        self.assertEqual(manifest, WHALE/'animation-video/animations.json')

    def test_pack_covers_state_machine_poses(self):
        animation = load_animation_pack(WHALE/'animation-video/animations.json')
        for pose in PRIORITY:
            if pose == 'falling':
                continue  # 掉落沿用拎起的动作
            self.assertIn(pose, animation.poses, pose)
        self.assertEqual(animation.poses['grabbed'], 'grabbed')
        self.assertIn(('grabbed', 'idle'), animation.transitions)
        self.assertIn(('walk', 'idle'), animation.transitions)

    def test_release_collects_every_referenced_atlas(self):
        files = {str(path).replace('\\', '/') for path in whale_art_files(WHALE)}
        animation = load_animation_pack(WHALE/'animation-video/animations.json')
        images = {clip.path.name for clip in animation.actions.values()}
        images |= {item.clip.path.name for item in animation.transitions.values()}
        for name in images | {'animations.json', animation.poster.name}:
            self.assertIn('animation-video/' + name, files)


if __name__ == '__main__':
    unittest.main()
