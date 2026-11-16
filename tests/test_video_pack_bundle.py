"""鲸鲸默认形象改为随程序发布的视频逐帧动画。"""
import shutil
import tempfile
import unittest
from pathlib import Path

from aicompanion.characters import PACKS_DIR, choose_renderer, installed_alternatives, load_pack
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

    def test_installed_aboutme_is_an_optional_alternative(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'deepseek-whale'
            root.mkdir()
            for name in ('character.json', 'lines.json', 'interjections.json', 'qq-chat-card.json'):
                shutil.copy2(WHALE/name, root/name)
            (root/'animation-video').mkdir()
            (root/'animation-video/animations.json').write_text('{}', encoding='utf-8')
            pack = load_pack(root)
            self.assertEqual(installed_alternatives(pack), [])
            # 没装 Aboutme 时，即使设置里选了它也回到默认动画。
            self.assertEqual(choose_renderer(pack, 'aboutme', '')[1], root/'animation-video/animations.json')
            (root/'animation-aboutme').mkdir()
            (root/'animation-aboutme/animations.json').write_text('{}', encoding='utf-8')
            self.assertEqual(installed_alternatives(pack), ['aboutme'])
            self.assertEqual(choose_renderer(pack, 'aboutme', '')[1], root/'animation-aboutme/animations.json')
            self.assertEqual(choose_renderer(pack, 'sprites', '')[1], root/'animation-video/animations.json')

    def test_pack_covers_state_machine_poses(self):
        animation = load_animation_pack(WHALE/'animation-video/animations.json')
        for pose in PRIORITY:
            self.assertIn(pose, animation.poses, pose)
        # 松手后还在空中时继续悬空乱蹬，落地后才播 grabbed→idle 落地过渡。
        self.assertEqual(animation.poses['falling'], 'grabbed')
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
