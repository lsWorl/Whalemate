"""v2 桌宠：角色包、注册表、状态机与台词节奏。纯 Python，不需要 Qt。"""
import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aicompanion.characters import (CharacterPackError, PACKS_DIR, discover, load_pack, load_sprite_manifest)
from aicompanion.characters.registry import ACTIVE_KEY, CharacterRegistry
from aicompanion.pet.behavior import LinePacer, greeting_key
from aicompanion.pet.state import PetSnapshot, PetStateMachine, reduce
from aicompanion.storage.database import Database


def settings(**overrides):
    values = dict(llm_provider='openai_compatible', llm_base_url='https://api.deepseek.com', llm_model='deepseek-flash',
                  llm_temperature=.8, llm_timeout_seconds=60)
    values.update(overrides)
    return SimpleNamespace(**values)


class CharacterPackTests(unittest.TestCase):
    def test_bundled_packs_load(self):
        packs, errors = discover()
        self.assertEqual(errors, [])
        ids = [pack.id for pack in packs]
        self.assertEqual(ids[0], 'deepseek-whale')
        self.assertNotIn('emilia', ids)   # 爱蜜莉雅已删除（2026-11-11 负责人决定）
        self.assertFalse(next(p for p in packs if p.id == 'claude-reader').ready)

    def test_whale_sprites_complete(self):
        pack = load_pack(PACKS_DIR/'deepseek-whale')
        data = load_sprite_manifest(pack.sprite_manifest_path())
        for pose in ['idle', 'talking', 'thinking', 'working', 'celebrate', 'error', 'wait', 'sleepy', 'grabbed', 'poked']:
            self.assertIn(pose, data['poses'])
        self.assertIn('eyes_closed_mouth_closed', data['poses']['talking'])
        self.assertIn('grab_point', data['poses']['grabbed'])

    def test_whale_has_lines_and_persona(self):
        pack = load_pack(PACKS_DIR/'deepseek-whale')
        self.assertIn('词元', pack.line('token_burn', random.Random(1)))
        self.assertNotIn('主人', pack.persona['description'])
        self.assertEqual(pack.line('missing-key'), '')

    def test_line_avoids_immediate_repeat(self):
        pack = load_pack(PACKS_DIR/'deepseek-whale')
        rng = random.Random(3)
        previous = pack.line('poke', rng)
        for _ in range(20):
            current = pack.line('poke', rng, avoid=previous)
            self.assertNotEqual(current, previous); previous = current

    def test_traversal_and_bad_format_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'bad'; root.mkdir()
            base = {'format': 'AICompanionCharacter', 'version': 1, 'id': 'bad', 'name': 'x', 'lines': '../secret.json'}
            (root/'character.json').write_text(json.dumps(base), encoding='utf-8')
            with self.assertRaises(CharacterPackError): load_pack(root)
            base.update(lines='', id='other')
            (root/'character.json').write_text(json.dumps(base), encoding='utf-8')
            with self.assertRaises(CharacterPackError): load_pack(root)
            (root/'character.json').write_text('{', encoding='utf-8')
            with self.assertRaises(CharacterPackError): load_pack(root)

    def test_missing_sprite_file_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'sprites.json'
            path.write_text(json.dumps({'format': 'AICompanionSpritePack', 'version': 1, 'size': [100, 200],
                'poses': {p: {'base': p+'.png'} for p in ('idle', 'talking', 'thinking')}}), encoding='utf-8')
            with self.assertRaises(CharacterPackError): load_sprite_manifest(path)


class RendererChoiceTests(unittest.TestCase):
    def test_choices(self):
        from aicompanion.characters import choose_renderer
        whale = load_pack(PACKS_DIR/'deepseek-whale')
        self.assertEqual(choose_renderer(whale, None)[0], 'sprites')
        self.assertEqual(choose_renderer(whale, 'live2d', '')[0], 'sprites')      # 旧默认值不再指向 Haru
        self.assertEqual(choose_renderer(whale, 'live2d', 'x.model3.json'), ('cubism', 'x.model3.json'))
        self.assertEqual(choose_renderer(None, 'live2d', ''), ('placeholder', None))
        # 爱蜜莉雅已删除：旧的立绘显示模式不再有效，没有形象包的角色显示占位
        self.assertEqual(choose_renderer(None, 'animated-illustration'), ('placeholder', None))

    def test_no_haru_references_left(self):
        root = PACKS_DIR.parent
        for path in list(root.rglob('*.py'))+list(root.rglob('*.html')):
            with self.subTest(path=path.name):
                self.assertNotIn('Haru/', path.read_text(encoding='utf-8'))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.database = Database(Path(self.temp.name)/'db.sqlite3'); self.database.initialize()

    def test_sync_adds_whale_once_and_defaults_to_it(self):
        registry = CharacterRegistry(self.database, settings(), environ={'QWEN_ORIGINAL_TTS_VOICE': 'voice-x'})
        registry.sync(); registry.sync()
        ids = [c.id for c in self.database.characters()]
        self.assertEqual(ids.count('deepseek-whale'), 1)
        self.assertNotIn('claude-reader', ids)
        self.assertEqual(registry.active().id, 'deepseek-whale')
        self.assertEqual(self.database.get_setting('speech_language:deepseek-whale'), 'zh')
        self.assertEqual(self.database.get_setting('qwen_voice:deepseek-whale'), 'voice-x')
        self.assertEqual(registry.characters()[0].id, 'deepseek-whale')

    def test_user_choice_survives_restart(self):
        CharacterRegistry(self.database, settings()).sync()
        from aicompanion.domain.character import CharacterProfile
        self.database.add_character(CharacterProfile('自建角色', '用户自己写的设定', '简短', 'custom-1'))
        self.database.set_setting(ACTIVE_KEY, 'custom-1')
        registry = CharacterRegistry(self.database, settings()); registry.sync()
        self.assertEqual(registry.active().id, 'custom-1')

    def test_placeholder_is_replaced_by_pack_persona_once(self):
        # 数据库初始化写的是占位角色（与鲸娘同 ID）；同步后换成角色包的名字和人设，用户改过的不再覆盖
        registry = CharacterRegistry(self.database, settings()); registry.sync()
        whale = next(c for c in self.database.characters() if c.id == 'deepseek-whale')
        self.assertEqual(whale.description, registry.pack('deepseek-whale').persona['description'])
        from dataclasses import replace
        self.database.update_character(replace(whale, description='我自己写的'))
        CharacterRegistry(self.database, settings()).sync()
        self.assertEqual(next(c for c in self.database.characters() if c.id == 'deepseek-whale').description, '我自己写的')

    def test_user_edits_and_history_are_kept(self):
        registry = CharacterRegistry(self.database, settings()); registry.sync()
        whale = next(c for c in self.database.characters() if c.id == 'deepseek-whale')
        from dataclasses import replace
        self.database.update_character(replace(whale, name='小鲸'))
        self.database.add_message('user', 'hello', 'deepseek-whale')
        CharacterRegistry(self.database, settings()).sync()
        self.assertEqual(next(c for c in self.database.characters() if c.id == 'deepseek-whale').name, '小鲸')
        self.assertEqual(self.database.recent_messages(5, 'deepseek-whale')[0]['content'], 'hello')



class StateMachineTests(unittest.TestCase):
    def test_chat_flow(self):
        machine = PetStateMachine(0)
        machine.dispatch('message_sent', 0)
        self.assertEqual(machine.current(0.0), 'idle')        # 最短停留
        self.assertEqual(machine.current(0.7), 'thinking')
        machine.dispatch('reply_token', 1.0)
        self.assertEqual(machine.current(1.0), 'thinking')
        self.assertEqual(machine.current(1.4), 'talking')
        machine.dispatch('reply_finished', 2.0, ok=True)
        self.assertEqual(machine.current(2.5), 'talking')     # 文字回复保留尾巴
        self.assertEqual(machine.current(3.5), 'idle')

    def test_voice_keeps_talking_until_playback_ends(self):
        machine = PetStateMachine(0)
        machine.dispatch('speaking', 0, active=True)
        machine.dispatch('reply_finished', 1, ok=True)
        self.assertEqual(machine.current(5), 'talking')
        machine.dispatch('speaking', 6, active=False)
        self.assertEqual(machine.current(7), 'idle')

    def test_priority_and_urgent_switch(self):
        machine = PetStateMachine(0)
        machine.dispatch('message_sent', 0); machine.current(1)
        machine.dispatch('grab', 1.1, active=True)
        self.assertEqual(machine.current(1.1), 'grabbed')     # 立即切换
        machine.dispatch('error', 1.2, message='x')
        self.assertEqual(machine.current(1.2), 'grabbed')   # 被拎在半空时，慌张优先于出错
        machine.dispatch('grab', 2, active=False)
        self.assertEqual(machine.current(2), 'error')

    def test_error_expires_and_clears(self):
        snapshot, effects = reduce(PetSnapshot(), 'reply_finished', 0, ok=False, error='HTTP 500')
        self.assertEqual(effects, ['error']); self.assertEqual(snapshot.pose(1), 'error')
        snapshot, _ = reduce(snapshot, 'tick', 9, idle_seconds=0, hour=12)
        self.assertEqual(snapshot.pose(9), 'idle'); self.assertEqual(snapshot.error, '')

    def test_poke_anger_and_wake(self):
        snapshot = PetSnapshot(); effects = []
        for i in range(6):
            snapshot, effects = reduce(snapshot, 'poke', i*.3)
        self.assertEqual(effects, ['poke_angry'])
        self.assertEqual(snapshot.pose(1.6), 'poked')
        snapshot, _ = reduce(snapshot, 'tick', 100, idle_seconds=4*60, hour=2)
        self.assertEqual(snapshot.pose(100), 'sleepy')
        snapshot, effects = reduce(snapshot, 'poke', 101)
        self.assertEqual(effects, ['wake'])

    def test_sleepy_depends_on_time_and_activity(self):
        snapshot, _ = reduce(PetSnapshot(), 'tick', 0, idle_seconds=4*60, hour=14)
        self.assertFalse(snapshot.sleepy)
        snapshot, _ = reduce(PetSnapshot(), 'tick', 0, idle_seconds=21*60, hour=14)
        self.assertTrue(snapshot.sleepy)
        busy, _ = reduce(PetSnapshot(), 'message_sent', 0)
        busy, _ = reduce(busy, 'tick', 0, idle_seconds=99999, hour=3)
        self.assertFalse(busy.sleepy)

    def test_work_success_celebrates_and_failure_errors(self):
        snapshot, effects = reduce(PetSnapshot(), 'work_started', 0, tool='files', task='整理')
        self.assertEqual(snapshot.pose(0), 'working'); self.assertEqual(effects, ['working'])
        done, effects = reduce(snapshot, 'work_finished', 1, ok=True)
        self.assertEqual(done.pose(1.5), 'celebrate'); self.assertEqual(effects, ['celebrate'])
        failed, effects = reduce(snapshot, 'work_finished', 1, ok=False, error='x')
        self.assertEqual(failed.pose(1.5), 'error')

    def test_pat_shows_patted_and_wakes(self):
        machine = PetStateMachine(0)
        effects = machine.dispatch('pat', 1)
        self.assertEqual(effects, ['pat']); self.assertEqual(machine.current(1), 'patted')
        self.assertEqual(machine.current(3.5), 'idle')
        snapshot, _ = reduce(PetSnapshot(sleepy=True), 'pat', 0)
        self.assertFalse(snapshot.sleepy)

    def test_throw_fall_and_dizzy(self):
        machine = PetStateMachine(0)
        machine.dispatch('grab', 0, active=True)
        effects = machine.dispatch('fall', 1, active=True, thrown=True)
        self.assertEqual(effects, ['thrown']); self.assertEqual(machine.current(1), 'falling')
        self.assertFalse(machine.snapshot.grabbed)
        self.assertEqual(machine.dispatch('landed', 2, hard=True), ['dizzy'])
        self.assertEqual(machine.current(2), 'dizzy'); self.assertEqual(machine.current(5), 'idle')
        self.assertEqual(machine.dispatch('landed', 6, hard=False), ['dropped'])

    def test_unknown_event_is_ignored(self):
        snapshot = PetSnapshot(thinking=True)
        self.assertIs(reduce(snapshot, 'something-new', 0, x=1)[0], snapshot)


class PacerTests(unittest.TestCase):
    def test_cooldowns_and_night_quiet(self):
        pacer = LinePacer()
        self.assertTrue(pacer.allow('poke', 0, 12))
        self.assertFalse(pacer.allow('poke', 0.5, 12))
        self.assertTrue(pacer.allow('poke', 3, 12))
        self.assertFalse(pacer.allow('thinking', 4, 12))     # 与上一句间隔不足
        self.assertTrue(pacer.allow('thinking', 20, 12))
        self.assertFalse(pacer.allow('thinking', 100, 2))    # 深夜不主动说
        self.assertTrue(pacer.allow('poke', 200, 2))         # 用户戳她仍然回应
        self.assertFalse(pacer.allow('celebrate', 300, 12, quiet=True))

    def test_welcome_back_after_away(self):
        pacer = LinePacer()
        self.assertEqual(pacer.system_idle(30), '')
        self.assertEqual(pacer.system_idle(11*60), '')
        self.assertEqual(pacer.system_idle(1), 'welcome_back')
        self.assertEqual(pacer.system_idle(1), '')

    def test_greeting_by_hour(self):
        self.assertEqual([greeting_key(h) for h in (8, 12, 18, 1)], ['greet_morning', 'greet_noon', 'greet_evening', 'greet_night'])


if __name__ == '__main__': unittest.main()
