"""模型包结构和桌宠屏幕约束测试，不下载或运行第三方模型。"""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage

from aicompanion.avatar.model_package import validate_model_package, ModelPackageError
from aicompanion.ui.pet_geometry import fit_pet_geometry
from aicompanion.ui.desktop_pet import DesktopPetWindow
from aicompanion.ui.settings_window import SettingsWindow


class ModelPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # 仅用于结构校验夹具，不能由此声称它是可渲染的模型。
        (self.root/'test.moc3').write_bytes(b'MOC3\x03\x00\x00\x00')
        (self.root/'texture.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        self.data = {'Version': 3, 'FileReferences': {'Moc': 'test.moc3', 'Textures': ['texture.png']}}
        self.entry = self.root/'test.model3.json'

    def validate(self):
        self.entry.write_text(json.dumps(self.data), encoding='utf-8')
        return validate_model_package(self.entry)

    def test_missing_core_and_texture_are_rejected(self):
        for field, value in [('Moc', 'missing.moc3'), ('Textures', ['missing.png'])]:
            with self.subTest(field=field):
                original = self.data['FileReferences'][field]
                self.data['FileReferences'][field] = value
                with self.assertRaises(ModelPackageError): self.validate()
                self.data['FileReferences'][field] = original

    def test_remote_absolute_and_traversal_paths_rejected(self):
        for path in ['../test.moc3', 'https://example.com/model.moc3', 'C:/secret.moc3', '//server/share/a.moc3', 'folder\\a.moc3', '%2e%2e/test.moc3', 'test.moc3?query=1', 'test.moc3#fragment']:
            self.data['FileReferences']['Moc'] = path
            with self.subTest(path=path), self.assertRaises(ModelPackageError): self.validate()

    def test_renamed_image_and_newer_moc_rejected(self):
        for content in [b'not a model', b'MOC3\xff\x00\x00\x00']:
            (self.root/'test.moc3').write_bytes(content)
            with self.assertRaises(ModelPackageError): self.validate()

    def test_missing_motion_or_expression_rejected(self):
        self.data['FileReferences']['Motions'] = {'Idle': [{'File': 'missing.motion3.json'}]}
        with self.assertRaises(ModelPackageError): self.validate()
        self.data['FileReferences'].pop('Motions')
        self.data['FileReferences']['Expressions'] = [{'Name': 'happy'}]
        with self.assertRaises(ModelPackageError): self.validate()

    def test_invalid_json_and_old_format_rejected(self):
        self.entry.write_text('{', encoding='utf-8')
        with self.assertRaises(ModelPackageError): validate_model_package(self.entry)
        self.data['Version'] = 2
        with self.assertRaises(ModelPackageError): self.validate()

    def test_empty_texture_and_wrong_manifest_type_rejected(self):
        self.data['FileReferences']['Textures'] = []
        with self.assertRaises(ModelPackageError): self.validate()
        self.entry.write_text('[]', encoding='utf-8')
        with self.assertRaises(ModelPackageError): validate_model_package(self.entry)


class PetGeometryTests(unittest.TestCase):
    def test_missing_or_invalid_preferences_use_visible_defaults(self):
        for saved in [None, [], ['wrong'], [0, 0, -3, 900], ['nan', 0, 340, 540]]:
            with self.subTest(saved=saved):
                x, y, w, h = fit_pet_geometry(saved, [(0, 0, 1920, 1040)])
                self.assertTrue(0<=x<=1920-w and 0<=y<=1040-h)

    def test_negative_coordinate_secondary_monitor_is_retained(self):
        value = (-1500, 100, 340, 540)
        self.assertEqual(fit_pet_geometry(value, [(0, 0, 1920, 1040), (-1920, 0, 1920, 1040)]), value)

    def test_removed_monitor_and_oversized_window_recover(self):
        x, y, w, h = fit_pet_geometry((4000, -900, 1600, 1800), [(0, 0, 1280, 720)])
        self.assertEqual((x, y, w, h), (0, 0, 1280, 720))

    def test_drag_clamps_at_screen_edge(self):
        self.assertEqual(fit_pet_geometry((-100, -30, 340, 540), [(0, 0, 1920, 1040)]), (0, 0, 340, 540))


class PetInteractionTests(unittest.TestCase):
    def setUp(self):
        self.pet = SimpleNamespace(_drag_offset=None, pos=Mock(return_value=QPoint(100, 200)), move=Mock(), constrain_geometry=Mock())  # WebEngine 视图的拖动路径

    def test_drag_uses_native_screen_offset(self):
        with patch('aicompanion.ui.desktop_pet.QCursor.pos', return_value=QPoint(130, 240)):
            DesktopPetWindow._pointer(self.pet, 'press')
        with patch('aicompanion.ui.desktop_pet.QCursor.pos', return_value=QPoint(330, 440)):
            DesktopPetWindow._pointer(self.pet, 'move')
        self.pet.move.assert_called_once_with(QPoint(300, 400))

    def test_release_resets_drag_and_recovers_screen_position(self):
        self.pet._drag_offset = QPoint(10, 20)
        DesktopPetWindow._pointer(self.pet, 'release')
        self.assertIsNone(self.pet._drag_offset)
        self.pet.constrain_geometry.assert_called_once()

    def test_move_without_press_is_ignored(self):
        DesktopPetWindow._pointer(self.pet, 'move')
        self.pet.move.assert_not_called()

    def test_typed_invalid_model_path_is_rejected(self):
        dialog = SimpleNamespace(avatar_mode=Mock(), model=Mock())
        dialog.avatar_mode.currentData.return_value='live2d'
        dialog.model.text.return_value='missing.model3.json'
        with patch('aicompanion.ui.settings_window.QMessageBox.warning') as warning:
            self.assertFalse(SettingsWindow._validate_model_input(dialog))
        warning.assert_called_once()

    def test_empty_model_path_is_allowed(self):
        dialog = SimpleNamespace(avatar_mode=Mock(), model=Mock())
        dialog.avatar_mode.currentData.return_value='live2d'; dialog.model.text.return_value=''
        self.assertTrue(SettingsWindow._validate_model_input(dialog))


class AnimatedIllustrationTests(unittest.TestCase):
    def test_generated_textures_are_registered_and_transparent(self):
        root=Path(__file__).resolve().parents[1]/'src/aicompanion/assets/live2d/Emilia'
        config=json.loads((root/'emilia.animation.json').read_text(encoding='utf-8'))
        self.assertEqual(config['Engine'],'pixi-mesh-not-cubism')
        for key,name in config['Textures'].items():
            image=QImage(str(root/name))
            with self.subTest(name=name):
                self.assertEqual((image.width(),image.height()),tuple(config['Canvas']))
                self.assertTrue(image.hasAlphaChannel())
                # 全幅透明性由底图决定，生成的眼嘴纹理只在脸部遮罩内采样。
                if key=='Base': self.assertEqual(image.pixelColor(0,0).alpha(),0)

    def test_animation_manifest_is_not_accepted_as_cubism(self):
        path=Path(__file__).resolve().parents[1]/'src/aicompanion/assets/live2d/Emilia/emilia.animation.json'
        with self.assertRaises(ModelPackageError): validate_model_package(path)


if __name__ == '__main__': unittest.main()
