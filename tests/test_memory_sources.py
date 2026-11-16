"""T26：记忆来源合并、按来源删除与整理期间的删除。全部为合成数据。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.voice_profile import REMOVED_MALE_VOICES, WHALE
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database

CID = DEFAULT_CHARACTER.id


class MemorySourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Database(Path(directory.name) / 'test.sqlite3')
        self.db.initialize()

    def sources(self, key):
        row = next(r for r in self.db.memory_card(CID) if r['fact_key'] == key)
        return json.loads(row['source_ids'])

    def test_repeated_fact_merges_sources_and_survives_partial_deletion(self):
        first = self.db.add_message('user', '我喜欢松果')
        second = self.db.add_message('user', '说过了，我真的喜欢松果')
        self.db.upsert_memory('用户喜欢松果', fact_key='like:pine', source_ids=[first])
        self.db.upsert_memory('用户喜欢松果', fact_key='like:pine', source_ids=[second])
        self.assertEqual(self.sources('like:pine'), [first, second])
        self.db.delete_chat_messages(CID, [second])
        self.assertEqual(self.sources('like:pine'), [first])
        self.assertEqual([json.loads(r['source_ids']) for r in self.db.memory_archive(CID)], [[first]])
        self.db.delete_chat_messages(CID, [first])
        self.assertFalse(self.db.memory_card(CID))
        self.assertFalse(self.db.memory_archive(CID))

    def test_deleting_either_mention_keeps_card_and_archive_in_step(self):
        for doomed in (0, 1):
            with self.subTest(deleted='first' if doomed == 0 else 'second'):
                ids = [self.db.add_message('user', f'我喜欢松果{doomed}{i}') for i in range(2)]
                key = f'like:pine{doomed}'
                for mid in ids:
                    self.db.upsert_memory('用户喜欢松果', fact_key=key, source_ids=[mid])
                self.db.delete_chat_messages(CID, [ids[doomed]])
                survivor = [ids[1 - doomed]]
                self.assertEqual(self.sources(key), survivor)
                archived = [json.loads(r['source_ids']) for r in self.db.memory_archive(CID) if r['fact_key'] == key]
                self.assertEqual(archived, [survivor])

    def test_correction_does_not_inherit_old_sources(self):
        shanghai = self.db.add_message('user', '我住上海')
        hangzhou = self.db.add_message('user', '搬家了，现在住杭州')
        self.db.upsert_memory('用户住在上海', fact_key='home', source_ids=[shanghai])
        self.db.upsert_memory('用户住在杭州', fact_key='home', source_ids=[hangzhou])
        self.assertEqual(self.sources('home'), [hangzhou])
        self.db.delete_chat_messages(CID, [hangzhou])
        self.assertFalse([r for r in self.db.memory_card(CID) if '杭州' in r['content']])
        self.assertFalse([r for r in self.db.memory_archive(CID) if '杭州' in r['content']])

    def test_iterator_sources_are_kept(self):
        first = self.db.add_message('user', '我喜欢松果')
        second = self.db.add_message('user', '我还是喜欢松果')
        self.db.upsert_memory('用户喜欢松果', fact_key='pine', source_ids=iter([first]))
        self.db.upsert_memory('用户喜欢松果', fact_key='pine', source_ids=iter([second]))
        self.assertEqual(self.sources('pine'), [first, second])
        self.db.delete_chat_messages(CID, [first, second])
        self.assertFalse(self.db.memory_card(CID))

    def test_sources_from_another_channel_are_not_mixed(self):
        desktop = self.db.add_message('user', '我喜欢松果')
        group = self.db.add_message('user', '我喜欢松果', channel='qq:group:12')
        self.db.upsert_memory('用户喜欢松果', fact_key='like:pine', source_ids=[desktop])
        self.db.upsert_memory('用户喜欢松果', fact_key='like:pine', source_ids=[group])
        self.assertEqual(self.sources('like:pine'), [group])

    def test_merged_sources_are_capped(self):
        ids = [self.db.add_message('user', f'第{i}次说松果') for i in range(Database.MAX_MEMORY_SOURCES + 5)]
        for mid in ids:
            self.db.upsert_memory('用户喜欢松果', fact_key='like:pine', source_ids=[mid])
        self.assertEqual(self.sources('like:pine'), ids[-Database.MAX_MEMORY_SOURCES:])

    def test_deleting_one_message_during_analysis_keeps_unrelated_memories(self):
        kept = self.db.add_message('user', '测试代号松果')
        dropped = self.db.add_message('user', '测试代号橡果')
        def generate(*args):
            self.db.delete_chat_messages(CID, [dropped])
            yield json.dumps({'memories': [
                dict(content='用户喜欢松果', fact_key='pine', category='preference', source_ids=[kept]),
                dict(content='用户喜欢橡果', fact_key='acorn', category='preference', source_ids=[dropped])]})
        MemoryService(self.db).analyze(CID, SimpleNamespace(generate_reply_stream=generate), CancellationToken())
        self.assertEqual([r['fact_key'] for r in self.db.memory_card(CID)], ['pine'])

    def test_male_voices_are_not_offered(self):
        offered = {voice for voice, _ in WHALE.minimax_candidates}
        self.assertFalse(offered & REMOVED_MALE_VOICES)
        self.assertIn(WHALE.minimax_voice, offered)


if __name__ == '__main__':
    unittest.main()
