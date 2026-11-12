"""Codex PR #41 审查的三个 P2 的组合用例：FixedRouter 缺默认声线接口、首次接入覆盖明确的本地朗读、试听提前保存音色。
不联网、不需要音频设备：MiniMax 账户只存假 Key，试听只看入队的合成配置，不真的合成。"""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QPushButton
except ImportError:   # pragma: no cover
    QApplication = None

from aicompanion.config import Settings
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.voice_profile import WHALE, adopt_default_voice
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.models import PROVIDER_PRESETS
from aicompanion.providers.router import FixedRouter, ModelRouter
from aicompanion.providers.store import ModelConfigStore
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database
from test_breeze_tts import FakeProtector

WHALE_ID = 'deepseek-whale'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'t.sqlite3'); self.db.initialize()
        self.creds = CredentialStore(self.db, {}, protector=FakeProtector())
        self.router = ModelRouter(self.db, SimpleNamespace(offline=False), credentials=self.creds)

    def tearDown(self): self.tmp.cleanup()

    def add_minimax(self):
        ref = self.creds.save('minimax-global', 'mm-1')
        ModelConfigStore(self.db).put_provider(replace(PROVIDER_PRESETS['minimax-global'], credential=ref))


class FixedRouterTests(Base):
    def test_fixed_router_matches_voice_interface(self):
        # 审查 1：界面对 router 调用的语音接口，FixedRouter 都要有，并安全地返回“没有云端声线”
        fixed = FixedRouter(self.db, MockLLMClient())
        for name in ('tts_target', 'default_voice'):
            with self.subTest(name=name): self.assertTrue(callable(getattr(fixed, name, None)))
        self.assertIsNone(fixed.default_voice(WHALE_ID)); self.assertIsNone(fixed.tts_target(WHALE_ID, voice_choice='Arrogant_Miss'))
        self.assertFalse(adopt_default_voice(self.db, fixed, WHALE_ID))
        self.assertIsNone(self.db.get_setting('tts_provider'))
        self.assertFalse(adopt_default_voice(self.db, SimpleNamespace(), WHALE_ID))      # 连方法都没有的旧解析器也不报错


class AdoptDefaultVoiceTests(Base):
    def test_unset_library_adopts_once(self):
        self.assertFalse(adopt_default_voice(self.db, self.router, WHALE_ID))           # 还没有 MiniMax 账户：先不切，也不打标记
        self.assertIsNone(self.db.get_setting('voice_profile_setup'))
        self.add_minimax()
        self.assertTrue(adopt_default_voice(self.db, self.router, WHALE_ID))
        self.assertEqual((self.db.get_setting('tts_provider'), self.db.get_setting('voice_profile_setup')), ('breeze', WHALE_ID))
        self.db.set_setting('tts_provider', 'system')                                     # 之后用户在设置里改回本地朗读
        self.assertFalse(adopt_default_voice(self.db, self.router, WHALE_ID))
        self.assertEqual(self.db.get_setting('tts_provider'), 'system')

    def test_explicit_local_speech_is_kept(self):
        # 审查 2：库里明确存着 system（不计费、文本不上传），配了 MiniMax 账户后也不能被改成云端
        self.db.set_setting('tts_provider', 'system'); self.add_minimax()
        self.assertFalse(adopt_default_voice(self.db, self.router, WHALE_ID))
        self.assertEqual(self.db.get_setting('tts_provider'), 'system'); self.assertIsNone(self.db.get_setting('voice_profile_setup'))
        for stored in ('qwen', 'cloud'):
            self.db.set_setting('tts_provider', stored)
            self.assertFalse(adopt_default_voice(self.db, self.router, WHALE_ID)); self.assertEqual(self.db.get_setting('tts_provider'), stored)

    def test_environment_choice_is_respected(self):
        self.add_minimax()
        self.assertFalse(adopt_default_voice(self.db, self.router, WHALE_ID, env_provider='qwen'))
        self.assertIsNone(self.db.get_setting('tts_provider'))


@unittest.skipIf(QApplication is None, '需要 PySide6')
class WindowTests(Base):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.settings = Settings('AICompanion', self.db.database_path, offline=False)

    def apply_settings(self, router):
        """调用生产的 MainWindow._apply_settings；窗口里和声音无关的部件用替身，不创建真窗口和音频设备。"""
        from aicompanion.ui.main_window import MainWindow
        controller = ChatController(self.db, MemoryService(self.db), router)
        controller.character = self.db.characters()[0]
        toggle = QPushButton(); toggle.setCheckable(True)
        fake = SimpleNamespace(settings=self.settings, database=self.db, controller=controller,
                               speech_client=SimpleNamespace(asr_provider=''), speech=SimpleNamespace(),
                               speak_toggle=toggle, pet=SimpleNamespace(show_bubble=lambda *a: None),
                               _apply_identity=lambda: None, _apply_avatar=lambda: None, _apply_backdrop=lambda: None)
        MainWindow._apply_settings(fake)
        return fake

    def test_production_apply_settings_with_fixed_router(self):
        # 审查 1：离线自检 / 预览窗口用 FixedRouter，生产 _apply_settings 不能 AttributeError
        fake = self.apply_settings(FixedRouter(self.db, MockLLMClient()))
        self.assertEqual(fake.speech.provider, 'system'); self.assertIsNone(self.db.get_setting('tts_provider'))

    def test_production_apply_settings_keeps_explicit_system(self):
        self.db.set_setting('tts_provider', 'system'); self.add_minimax()
        fake = self.apply_settings(self.router)
        self.assertEqual((fake.speech.provider, self.db.get_setting('tts_provider')), ('system', 'system'))

    def test_production_apply_settings_new_library_uses_whale_voice(self):
        self.add_minimax()
        fake = self.apply_settings(self.router)
        self.assertEqual(fake.speech.provider, 'breeze')
        self.assertIn(WHALE.minimax_voice, fake.speech.voice)                                 # cache_id 里带着默认音色

    def settings_window(self, speech_output=None):
        import aicompanion.ui.settings_window as module
        controller = ChatController(self.db, MemoryService(self.db), self.router)
        controller.character = self.db.characters()[0]
        with mock.patch.object(module, 'QMediaDevices', SimpleNamespace(audioInputs=lambda: [])):
            return module.SettingsWindow(controller, self.settings, speech_output=speech_output, page='voice')

    def test_preview_and_close_flushes_selected_voice(self):
        # 试听沿用已选音色，防抖期间关闭窗口也要保存。
        self.add_minimax(); self.db.set_setting('tts_provider', 'breeze')

        class FakeSpeech(QObject):
            error = Signal(str); speaking = Signal(bool)
            def __init__(self):
                super().__init__(); self.queued = []
            def stop(self): pass
            def enqueue(self, text, generation_id='', target=None): self.queued.append(target)

        speech = FakeSpeech()
        dialog = self.settings_window(speech)
        try:
            self.assertEqual(dialog.provider.currentData(), 'breeze')
            self.assertEqual(dialog.voice.currentData(), 'choice:'+WHALE.minimax_voice)
            dialog.voice.setCurrentIndex(dialog.voice.findData('choice:Arrogant_Miss'))
            dialog.preview_voice()
            self.assertEqual(speech.queued[-1].voice, 'Arrogant_Miss')                       # 试听的确是候选 B
            self.assertIsNone(self.db.get_setting(f'voice_choice:{WHALE_ID}'))               # 但没有写库
            dialog.reject()
        finally:
            dialog.deleteLater()
        self.assertEqual(self.db.get_setting(f'voice_choice:{WHALE_ID}'), 'Arrogant_Miss')
        self.assertEqual(self.router.tts_target(WHALE_ID).voice, 'Arrogant_Miss')

        dialog = self.settings_window()
        try:
            dialog.voice.setCurrentIndex(dialog.voice.findData('choice:Arrogant_Miss'))
            dialog.save()
        finally:
            dialog.deleteLater()
        self.assertEqual(self.db.get_setting(f'voice_choice:{WHALE_ID}'), 'Arrogant_Miss')
        self.assertEqual(self.router.tts_target(WHALE_ID).voice, 'Arrogant_Miss')             # 保存后才是 B

    def test_saving_untouched_provider_keeps_it_unset(self):
        # 没存过朗读方式、用户也没改：保存别的设置不应把它写成“明确选了本地朗读”，否则之后加 MiniMax 账户也用不上默认声线
        dialog = self.settings_window()
        try:
            dialog.quiet_start.setValue(22); dialog.save()
        finally:
            dialog.deleteLater()
        self.assertEqual(self.db.get_setting('quiet_start'), 22); self.assertIsNone(self.db.get_setting('tts_provider'))
        dialog = self.settings_window()
        try:
            dialog.provider.setCurrentIndex(dialog.provider.findData('qwen')); dialog.save()
        finally:
            dialog.deleteLater()
        self.assertEqual(self.db.get_setting('tts_provider'), 'qwen')                          # 改过的照常保存

    def choose_provider_and_save(self, *values):
        dialog = self.settings_window()
        try:
            for value in values: dialog.provider.setCurrentIndex(dialog.provider.findData(value))
            dialog.save()
        finally:
            dialog.deleteLater()

    def test_switching_back_to_local_speech_is_an_explicit_choice(self):
        # Codex PR #41 复核：新库 → 设置窗口打开期间添加 MiniMax 账户 → 选千问再选回本地朗读 → 保存；
        # 首尾值相同，但这是明确选择，要落库，之后生产 _apply_settings 不能切到云端
        import aicompanion.ui.settings_window as module
        controller = ChatController(self.db, MemoryService(self.db), self.router)
        controller.character = self.db.characters()[0]
        with mock.patch.object(module, 'QMediaDevices', SimpleNamespace(audioInputs=lambda: [])):
            dialog = module.SettingsWindow(controller, self.settings, page='voice')
        try:
            self.assertEqual(dialog.provider.currentData(), 'system')
            self.add_minimax()
            dialog.provider.setCurrentIndex(dialog.provider.findData('qwen'))
            dialog.provider.setCurrentIndex(dialog.provider.findData('system'))
            dialog.save()
        finally:
            dialog.deleteLater()
        self.assertEqual(self.db.get_setting('tts_provider'), 'system')
        fake = self.apply_settings(self.router)
        self.assertEqual((fake.speech.provider, self.db.get_setting('tts_provider')), ('system', 'system'))
        self.assertIsNone(self.db.get_setting('voice_profile_setup'))

    def test_local_choice_saved_before_adding_account_is_kept(self):
        # 另一种顺序：先选回本地朗读并保存，以后才添加 MiniMax 账户
        self.choose_provider_and_save('qwen', 'system')
        self.assertEqual(self.db.get_setting('tts_provider'), 'system')
        self.add_minimax()
        fake = self.apply_settings(self.router)
        self.assertEqual((fake.speech.provider, self.db.get_setting('tts_provider')), ('system', 'system'))
        self.assertIsNone(self.db.get_setting('voice_profile_setup'))

    def test_untouched_new_library_still_adopts_after_save(self):
        # 完全没动朗读方式：保存后仍是“未选择”，加了 MiniMax 账户后照常接入默认声线
        self.choose_provider_and_save()
        self.assertIsNone(self.db.get_setting('tts_provider'))
        self.add_minimax()
        self.assertEqual(self.apply_settings(self.router).speech.provider, 'breeze')


if __name__ == '__main__':
    unittest.main()
