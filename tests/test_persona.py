"""计划书 v4 功能二：人格包。存取、切换、版本、角色包升级、导入导出（含 SillyTavern 角色卡）、进提示词。不联网。"""
import base64
import json
import struct
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aicompanion.domain.character import CharacterProfile
from aicompanion.domain.persona import (WARN_CHARS, Persona, PersonaError, export_own, export_sillytavern, prompt_extras,
                                        read_card)
from aicompanion.services.persona_service import KEEP_VERSIONS, PersonaService, builtin_id
from aicompanion.storage.database import Database

PACK = {'version': 3, 'description': '你是鲸娘，嘴硬心软。', 'speaking_style': '短句，偶尔自称本鲸。'}


def png_card(card: dict, key=b'chara') -> bytes:
    """最小的 PNG：签名 + IHDR + tEXt(chara=base64 JSON) + IEND。只为了测试读卡，不是能显示的图。"""
    def chunk(kind, body):
        return struct.pack('>I', len(body))+kind+body+struct.pack('>I', zlib.crc32(kind+body) & 0xffffffff)
    text = key+b'\0'+base64.b64encode(json.dumps(card, ensure_ascii=False).encode('utf-8'))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))+chunk(b'tEXt', text)+chunk(b'IEND', b'')


def png_chunks(*chunks) -> bytes:
    def chunk(kind, body):
        return struct.pack('>I', len(body))+kind+body+struct.pack('>I', zlib.crc32(kind+body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + b''.join(chunk(k, v) for k, v in chunks)+chunk(b'IEND', b''))


def ztxt(key: bytes, text: bytes) -> tuple:
    return b'zTXt', key+b'\0\0'+zlib.compress(text, 9)


def itxt(key: bytes, text: bytes) -> tuple:
    return b'iTXt', key+b'\0\x01\0'+b'\0\0'+zlib.compress(text, 9)   # 压缩标志 1、方法 0、空语言标签和译名


def card_text(card: dict, pad=0) -> bytes:
    data = dict(card, data=dict(card['data'], padding='x'*pad)) if pad else card
    return base64.b64encode(json.dumps(data, ensure_ascii=False).encode('utf-8'))


ST_CARD = {'spec': 'chara_card_v2', 'spec_version': '2.0', 'data': {
    'name': '别家角色', 'description': '{{char}}是个爱吐槽的猫娘。', 'personality': '毒舌但护短',
    'scenario': '{{user}}刚下班回家。', 'first_mes': '哟，回来了？', 'creator_notes': '测试卡',
    'mes_example': '<START>\n{{user}}: 我饿了\n{{char}}: 饿了就自己煮面\n<START>\n{{char}}: 哼，才不是担心{{user}}',
    'tags': ['猫', '吐槽'], 'character_version': '猫娘版',
    'character_book': {'entries': [{'keys': ['小鱼干'], 'content': '{{char}}最爱小鱼干', 'enabled': True},
                                   {'keys': [], 'content': '住在{{user}}家', 'constant': True, 'enabled': True},
                                   {'keys': ['秘密'], 'content': '不会出现', 'enabled': False}]}}}


class PersonaBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'p.sqlite3'); self.db.initialize()
        self.now = [1000.0]
        self.service = PersonaService(self.db, clock=lambda: self.now[0])
        self.whale = next(c for c in self.db.characters() if c.id == 'deepseek-whale')

    def tearDown(self): self.tmp.cleanup()

    def character(self):
        return next(c for c in self.db.characters() if c.id == 'deepseek-whale')


class ServiceTests(PersonaBase):
    def test_first_run_with_untouched_character_uses_builtin(self):
        self.db.update_character(replace(self.whale, description=PACK['description'], speaking_style=PACK['speaking_style']))
        active = self.service.ensure(self.character(), PACK)
        self.assertEqual((active.id, active.builtin, active.pack_version), (builtin_id('deepseek-whale'), True, 3))
        self.assertEqual(len(self.service.personas('deepseek-whale')), 1)
        self.assertEqual(self.service.ensure(self.character(), PACK).id, active.id)              # 再次启动不重复建

    def test_first_run_keeps_user_edits_as_their_own_persona(self):
        self.db.update_character(replace(self.whale, description='我自己写的设定', speaking_style='我自己写的风格'))
        active = self.service.ensure(self.character(), PACK)
        self.assertFalse(active.builtin); self.assertEqual(active.description, '我自己写的设定')
        built = self.service.get(builtin_id('deepseek-whale'))
        self.assertEqual(built.description, PACK['description'])                                 # 内置原文也在，随时可切回
        self.assertEqual(self.character().description, '我自己写的设定')

    def test_switch_changes_only_persona_fields_and_keeps_memories(self):
        self.service.ensure(self.character(), PACK)
        self.db.add_memory('喜欢冰奶茶', 0.9, 'deepseek-whale') if hasattr(self.db, 'add_memory') else None
        before = len(self.db.recent_memories(100, 'deepseek-whale'))
        tsun = self.service.create('deepseek-whale', {'title': '傲娇', 'description': '傲娇的鲸娘', 'speaking_style': '嘴上不饶人'})
        self.service.switch('deepseek-whale', tsun.id)
        now = self.character()
        self.assertEqual((now.name, now.description, now.speaking_style), (self.whale.name, '傲娇的鲸娘', '嘴上不饶人'))
        self.assertEqual(len(self.db.recent_memories(100, 'deepseek-whale')), before)
        self.assertEqual(self.service.active('deepseek-whale').id, tsun.id)
        with self.assertRaises(PersonaError): self.service.switch('other', tsun.id)

    def test_versions_revert_and_limit(self):
        self.service.ensure(self.character(), PACK)
        mine = self.service.create('deepseek-whale', {'title': 'A', 'description': 'v1', 'speaking_style': 's'})
        for n in range(2, KEEP_VERSIONS+5):
            self.now[0] += 1; mine = self.service.save(mine.with_content(dict(mine.content(), description=f'v{n}')))
        versions = self.service.versions(mine.id)
        self.assertEqual(len(versions), KEEP_VERSIONS); self.assertEqual(versions[0]['content']['description'], f'v{KEEP_VERSIONS+4}')
        same = self.service.save(mine); self.assertEqual(len(self.service.versions(mine.id)), KEEP_VERSIONS)   # 没改不留版本
        old = versions[3]
        reverted = self.service.revert(mine.id, old['id'])
        self.assertEqual(reverted.description, old['content']['description'])

    def test_pack_upgrade_respects_user_edits(self):
        self.db.update_character(replace(self.whale, description=PACK['description'], speaking_style=PACK['speaking_style']))
        self.service.ensure(self.character(), PACK)
        newer = dict(PACK, version=4, description='第四版设定')
        self.service.ensure(self.character(), newer)
        built = self.service.get(builtin_id('deepseek-whale'))
        self.assertEqual((built.description, built.pack_version), ('第四版设定', 4))
        self.assertEqual(self.character().description, '第四版设定')                              # 正在用的内置人格同步到角色
        self.service.save(built.with_content(dict(built.content(), description='我改了内置')))
        self.assertTrue(self.service.get(built.id).user_modified)
        self.service.ensure(self.character(), dict(PACK, version=5, description='第五版设定'))
        self.assertEqual(self.service.get(built.id).description, '我改了内置')                   # 改过的不覆盖
        self.assertTrue(self.service.pack_update_pending('deepseek-whale'))
        restored = self.service.restore_builtin('deepseek-whale', dict(PACK, version=5, description='第五版设定'))
        self.assertEqual((restored.description, restored.user_modified), ('第五版设定', False))
        self.assertFalse(self.service.pack_update_pending('deepseek-whale'))

    def test_delete_rules(self):
        self.service.ensure(self.character(), PACK)
        with self.assertRaises(PersonaError): self.service.delete(builtin_id('deepseek-whale'))
        other = self.service.duplicate(builtin_id('deepseek-whale'))
        self.service.switch('deepseek-whale', other.id)
        with self.assertRaises(PersonaError): self.service.delete(other.id)                   # 正在用的不能删
        self.service.switch('deepseek-whale', builtin_id('deepseek-whale'))
        self.service.delete(other.id)
        self.assertIsNone(self.service.get(other.id)); self.assertEqual(self.service.versions(other.id), [])

    def test_user_created_character_gets_default_persona(self):
        self.db.add_character(CharacterProfile('自建', '自建设定', '自建风格', 'custom-1'))
        custom = next(c for c in self.db.characters() if c.id == 'custom-1')
        active = self.service.ensure(custom)
        self.assertEqual((active.title, active.description, active.builtin), ('默认', '自建设定', False))


class CardTests(unittest.TestCase):
    def test_sillytavern_json_and_png(self):
        for raw in (json.dumps(ST_CARD).encode(), png_card(ST_CARD), png_card(ST_CARD, b'ccv3')):
            with self.subTest(kind=raw[:4]):
                data = read_card(raw)
                self.assertEqual(data['title'], '猫娘版')
                self.assertIn('你是个爱吐槽的猫娘', data['description']); self.assertIn('性格：毒舌但护短', data['description'])
                self.assertNotIn('{{', json.dumps(data, ensure_ascii=False, default=str))           # 宏都换掉了
                self.assertEqual(data['background'], '对方刚下班回家。')
                self.assertEqual([(e.user, e.reply) for e in data['examples']],
                                 [('', '哟，回来了？'), ('我饿了', '饿了就自己煮面'), ('', '哼，才不是担心对方')])
                self.assertEqual([(e.keys, e.always) for e in data['lore']], [(('小鱼干',), False), ((), True)])  # 禁用的条目不导入
                self.assertEqual(data['interests'], ('猫', '吐槽'))

    def test_compressed_png_text_is_bounded(self):
        # Codex PR #35 R3：zTXt / iTXt 解压无上限。普通压缩卡照常导入；解压量或多块累计超过上限时报错，不先整段解压
        from aicompanion.domain.persona import MAX_CARD_TEXT
        for chunk in (ztxt(b'chara', card_text(ST_CARD)), itxt(b'ccv3', card_text(ST_CARD))):
            with self.subTest(kind=chunk[0]):
                self.assertEqual(read_card(png_chunks(chunk))['title'], '猫娘版')
        bomb_text = card_text(ST_CARD, pad=6*1024*1024)                                       # 约 8 MB，压缩后只有几 KB
        for chunk in (ztxt(b'chara', bomb_text), itxt(b'ccv3', bomb_text)):
            with self.subTest(bomb=chunk[0]):
                raw = png_chunks(chunk)
                self.assertLess(len(raw), 64*1024)
                with self.assertRaisesRegex(PersonaError, '太大'): read_card(raw)
        half = card_text(ST_CARD, pad=int(MAX_CARD_TEXT*0.45))                                  # 单块不超，两块加起来超
        self.assertLess(len(half), MAX_CARD_TEXT)
        with self.assertRaisesRegex(PersonaError, '太大'):
            read_card(png_chunks(ztxt(b'chara', half), ztxt(b'ccv3', half)))
        with mock.patch('aicompanion.domain.persona.zlib.decompress', side_effect=AssertionError('不应整段解压')):
            self.assertEqual(read_card(png_chunks(ztxt(b'chara', card_text(ST_CARD))))['title'], '猫娘版')

    def test_own_format_round_trip_and_limits(self):
        persona = Persona('p', 'deepseek-whale', '').with_content({'title': '测试', 'description': '设定\x07', 'speaking_style': '风格',
            'examples': [{'user': '嗨', 'reply': '嗨嗨'}], 'lore': [{'keys': ['鱼'], 'content': '爱吃鱼'}],
            'temper': {'touchy': 2, 'forgiving': 'x'}, 'unknown': 'drop'})
        self.assertEqual(persona.description, '设定')                                          # 控制字符去掉
        self.assertEqual(dict(persona.temper), {'touchy': 1.0, 'forgiving': 0.5})               # 越界夹回、无效用默认
        data = read_card(json.dumps(export_own(persona)).encode())
        self.assertEqual(Persona('q', 'x', '').with_content(data).content(), persona.content())
        st = export_sillytavern(persona, '大肥鱼')
        self.assertEqual(st['data']['name'], '大肥鱼'); self.assertEqual(read_card(json.dumps(st).encode())['lore'], persona.lore)
        self.assertNotIn('memories', json.dumps(st)); self.assertNotIn('chat', json.dumps(st['data']).replace('chara', ''))
        with self.assertRaises(PersonaError): read_card(b'x'*(5*1024*1024))
        with self.assertRaises(PersonaError): read_card(b'not json')
        with self.assertRaises(PersonaError): read_card(b'\x89PNG\r\n\x1a\n')
        long = Persona('p', 'c', '').with_content({'title': 't', 'description': '长'*(WARN_CHARS+1), 'speaking_style': ''})
        self.assertGreater(long.chars(), WARN_CHARS)

    def test_prompt_extras_only_bring_matching_lore(self):
        persona = Persona('p', 'c', '').with_content({'title': 't', 'description': 'd', 'speaking_style': 's', 'background': '住在深海',
            'examples': [{'user': '你好', 'reply': '本鲸在'}],
            'lore': [{'keys': ['奶茶'], 'content': '只喝冰奶茶'}, {'keys': [], 'content': '怕打雷', 'always': True}]})
        plain = prompt_extras(persona, '今天天气不错')
        self.assertIn('背景：住在深海', plain); self.assertIn('你：本鲸在', plain); self.assertIn('怕打雷', plain)
        self.assertNotIn('冰奶茶', plain)
        self.assertIn('只喝冰奶茶', prompt_extras(persona, '要不要来杯奶茶'))
        self.assertEqual(prompt_extras(None, 'x'), '')


class ContextTests(PersonaBase):
    def test_active_persona_reaches_chat_prompt(self):
        from aicompanion.services.context_service import ContextService
        from aicompanion.services.memory_service import MemoryService
        self.service.ensure(self.character(), PACK)
        p = self.service.create('deepseek-whale', {'title': '海洋', 'description': '海洋设定', 'speaking_style': '海洋风格',
                                                  'background': '刚从马里亚纳海沟回来', 'lore': [{'keys': ['鲸落'], 'content': '鲸落是她的禁忌话题'}]})
        self.service.switch('deepseek-whale', p.id)
        _, prompt = ContextService(self.db, MemoryService(self.db)).build(self.character(), '你知道鲸落吗')
        self.assertIn('人设：海洋设定', prompt); self.assertIn('说话风格：海洋风格', prompt)
        self.assertIn('刚从马里亚纳海沟回来', prompt); self.assertIn('鲸落是她的禁忌话题', prompt)
        self.assertLess(prompt.index('刚从马里亚纳海沟回来'), prompt.index('当前心情'))
        _, other = ContextService(self.db, MemoryService(self.db)).build(self.character(), '今天吃什么')
        self.assertNotIn('鲸落是她的禁忌话题', other)


class RegistryTests(PersonaBase):
    def test_sync_creates_personas_and_later_pack_upgrade_goes_through_personas(self):
        from aicompanion.characters.registry import CharacterRegistry
        pack = SimpleNamespace(id='deepseek-whale', ready=True, name='鲸娘', order=1, voice={}, persona=dict(PACK))
        registry = CharacterRegistry(self.db, SimpleNamespace(), packs=[pack], environ={})
        registry.sync()
        self.assertEqual(self.character().description, PACK['description'])                    # 占位换成包里的人设
        self.assertTrue(self.service.get(builtin_id('deepseek-whale')).builtin)
        mine = self.service.create('deepseek-whale', {'title': '我的', 'description': '我的设定', 'speaking_style': '我的风格'})
        self.service.switch('deepseek-whale', mine.id)
        pack.persona = dict(PACK, version=9, description='包第九版', previous_description=PACK['description'],
                            previous_speaking_style=PACK['speaking_style'])
        registry.sync()
        self.assertEqual(self.character().description, '我的设定')                              # 正在用自己的人格：包升级不动角色
        self.assertEqual(self.service.get(builtin_id('deepseek-whale')).description, '包第九版')


try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:   # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, '需要 PySide6')
class EditorTests(PersonaBase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def editor(self, client=None):
        from aicompanion.ui.persona_page import PersonaEditor
        controller = SimpleNamespace(database=self.db, character=self.character(),
                                     client=lambda cid, purpose: client)
        sync = lambda function, ok, fail: ok(function(None))
        return PersonaEditor(controller, PACK, run=sync), controller

    def test_bond_fields_round_trip_and_preset_fills(self):
        editor, _ = self.editor()
        try:
            self.assertEqual(editor.bond_preset.currentData(), 'daughter'); self.assertEqual(editor.bond_call.text(), '爹')
            editor.bond_preset.setCurrentIndex(editor.bond_preset.findData('girlfriend'))
            self.assertEqual((editor.bond_call.text(), editor.bond_tag.text()), ('亲爱的', '对象'))
            editor.bond_call.setText('宝'); editor.save()
            saved = self.service.get(editor.current_id).relation()
            self.assertEqual((saved.preset, saved.call, saved.refer), ('girlfriend', '宝', '你的恋人'))
        finally:
            editor.deleteLater()

    def test_edit_save_switch_and_import(self):
        editor, controller = self.editor()
        try:
            editor.new(); editor.title.setText('工作模式'); editor.description.setPlainText('认真干活的鲸娘'); editor.style.setPlainText('简洁')
            editor._add_row(editor.lore, ('周报', '每周五要交周报', False)); editor.title.textEdited.emit('工作模式')
            self.assertTrue(editor.save())
            saved = self.service.get(editor.current_id)
            self.assertEqual((saved.title, saved.description, saved.lore[0].keys), ('工作模式', '认真干活的鲸娘', ('周报',)))
            self.assertNotEqual(controller.character.description, '认真干活的鲸娘')              # 还没设为当前
            editor.use()
            self.assertEqual(controller.character.description, '认真干活的鲸娘')
            self.assertEqual(self.service.active('deepseek-whale').id, saved.id)
            path = Path(self.tmp.name)/'card.png'; path.write_bytes(png_card(ST_CARD))
            editor.import_file(str(path))
            self.assertEqual(editor.title.text(), '猫娘版'); self.assertEqual(self.service.active('deepseek-whale').id, saved.id)  # 导入不自动切换
            out = Path(self.tmp.name)/'out.json'; editor.export_file('own', str(out))
            self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['title'], '猫娘版')
            editor.description.setPlainText('')
            with mock.patch.object(QMessageBox, 'warning') as warning: self.assertFalse(editor.save())
            warning.assert_called_once()
        finally:
            editor.deleteLater()

    def test_preview_uses_draft_without_saving(self):
        prompts = []
        client = SimpleNamespace(generate_reply_stream=lambda history, prompt, token: (prompts.append(prompt), iter(['哼，才没担心你']))[1])
        editor, _ = self.editor(client)
        try:
            editor.description.setPlainText('草稿设定'); editor.preview()
            self.assertIn('草稿设定', prompts[0]); self.assertIn('才没担心你', editor.preview_text.text())
            self.assertNotEqual(self.service.active('deepseek-whale').description, '草稿设定')     # 预览不保存
        finally:
            editor.deleteLater()

    def test_import_does_not_silently_drop_unsaved_draft(self):
        # Codex PR #35 R2：编辑中的草稿在导入前要问：取消 → 什么都不变；保存 → 草稿进了原人格；放弃 → 照常导入
        editor, _ = self.editor()
        path = Path(self.tmp.name)/'card.png'; path.write_bytes(png_card(ST_CARD))
        try:
            original = editor.current_id; count = len(self.service.personas('deepseek-whale'))
            editor.description.setPlainText('没保存的草稿'); self.assertTrue(editor.dirty)
            with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Cancel) as asked:
                editor.import_file(str(path))
            asked.assert_called_once()
            self.assertEqual((editor.current_id, editor.description.toPlainText(), editor.dirty), (original, '没保存的草稿', True))
            self.assertEqual(len(self.service.personas('deepseek-whale')), count)                    # 取消就不导入
            bad = Path(self.tmp.name)/'bad.json'; bad.write_text('不是 JSON', encoding='utf-8')
            with mock.patch.object(QMessageBox, 'warning'), mock.patch.object(QMessageBox, 'question') as asked:
                editor.import_file(str(bad))
            asked.assert_not_called()                                                            # 读不进来：不问也不动草稿
            self.assertEqual(editor.description.toPlainText(), '没保存的草稿')
            with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Save):
                editor.import_file(str(path))
            self.assertEqual(self.service.get(original).description, '没保存的草稿')            # 草稿先存进原人格
            self.assertEqual(editor.title.text(), '猫娘版'); self.assertEqual(len(self.service.personas('deepseek-whale')), count+1)
            editor.description.setPlainText('又一份草稿')
            with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Discard):
                editor.import_file(str(path))
            self.assertEqual(len(self.service.personas('deepseek-whale')), count+2); self.assertFalse(editor.dirty)
        finally:
            editor.deleteLater()

    def test_preview_is_labeled_and_cleared_when_persona_changes(self):
        # Codex PR #35 复审建议：换人格后旧预览要清掉；预览标明是哪个人格；换人格前发出、换人格后才回来的结果不显示
        pending = []
        client = SimpleNamespace(generate_reply_stream=lambda history, prompt, token: iter(['哼']))
        editor, _ = self.editor(client)
        try:
            first = editor.current_id; title = editor.title.text()
            editor.preview()
            self.assertIn(f'（{title}）：哼', editor.preview_text.text())
            editor.new()                                                                         # 换到另一个人格
            self.assertEqual(editor.preview_text.text(), '')
            editor.run = lambda function, ok, fail: pending.append((function, ok))              # 改成异步：结果晚点才回来
            editor.preview()
            self.assertIn('在想怎么回', editor.preview_text.text())
            editor.picker.setCurrentIndex(editor.picker.findData(first))                         # 结果回来前又换回去
            function, ok = pending.pop(); ok(function(None))
            self.assertEqual(editor.current_id, first); self.assertEqual(editor.preview_text.text(), '')   # 迟到的结果被丢掉
            editor.run = lambda function, ok, fail: ok(function(None)); editor.preview()           # 同一个人格保存后预览保留
            text = editor.preview_text.text(); editor.title.setText(title+'2'); editor.title.textEdited.emit(title+'2'); editor.save()
            self.assertEqual(editor.preview_text.text(), text)
        finally:
            editor.deleteLater()

    def test_preview_respects_daily_quota(self):
        # Codex PR #35 R1：预览也是聊天请求，额度到限就不发
        calls = []
        client = SimpleNamespace(generate_reply_stream=lambda history, prompt, token: (calls.append(prompt), iter(['嗯']))[1])
        self.db.set_setting('quota_daily_total', '0.5')
        self.db.add_token_usage('companion', 'deepseek-flash', 1000, 1000, cost=1.0, character_id='deepseek-whale')
        editor, _ = self.editor(client)
        try:
            editor.preview()
            self.assertEqual(calls, []); self.assertIn('已用完', editor.preview_text.text())
            self.db.set_setting('quota_daily_total', '5')
            editor.preview(); self.assertEqual(len(calls), 1)                                    # 额度够时照常预览
        finally:
            editor.deleteLater()


if __name__ == '__main__':
    unittest.main()
