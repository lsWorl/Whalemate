"""计划书 v4 功能六（阶段二）：记忆召回基础与整份请求预算。不联网、不调真实模型。"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.llm.base import ChatTurn
from aicompanion.services import budget
from aicompanion.services.context_service import ContextService
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.recall import RecallService, ago, score
from aicompanion.storage import fts
from aicompanion.storage.database import Database

CID = 'deepseek-whale'


class Model:
    def __init__(self): self.calls = []
    def generate_reply_stream(self, history, prompt, cancellation):
        self.calls.append((list(history), prompt)); yield '嗯嗯'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'r.sqlite3'
        self.db = Database(self.path); self.db.initialize()
        self.recall = RecallService(self.db)

    def tearDown(self):
        # 生成一轮后，关系分析会在后台线程里写库；等它写完再删临时目录（否则偶发“目录不为空”）
        import threading
        for thread in threading.enumerate():
            if thread.name.startswith('aicompanion-'): thread.join(5)
        self.tmp.cleanup()

    def fill_chat(self, n, channel='desktop', text='闲聊第{}句'):
        return [self.db.add_message('user' if i % 2 == 0 else 'assistant', text.format(i), CID, channel=channel) for i in range(n)]


class FtsTests(Base):
    def test_index_follows_insert_update_delete(self):
        mid = self.db.add_message('user', '周末去水族馆看白鲸', CID)
        with self.db.transaction() as c:
            self.assertEqual([r['id'] for r in fts.search(c, ['水族馆'], CID, 'desktop', 1 << 40)], [mid])
            c.execute("UPDATE chat_messages SET content='周末去爬山' WHERE id=?", (mid,))
            self.assertEqual(fts.search(c, ['水族馆'], CID, 'desktop', 1 << 40), [])
            self.assertEqual(len(fts.search(c, ['去爬山'], CID, 'desktop', 1 << 40)), 1)
            c.execute('DELETE FROM chat_messages WHERE id=?', (mid,))
            self.assertEqual(fts.search(c, ['去爬山'], CID, 'desktop', 1 << 40), [])
            self.assertEqual(fts.search(c, ['"引号"', 'OR'], CID, 'desktop', 1 << 40), [])     # 特殊字符不当成语法

    def test_old_database_is_indexed_on_upgrade(self):
        mid = self.db.add_message('user', '我养了一只叫团子的橘猫', CID)
        with closing(sqlite3.connect(self.path)) as c:
            for name in ('message_fts_ai', 'message_fts_ad', 'message_fts_au'): c.execute(f'DROP TRIGGER {name}')
            c.execute('DROP TABLE message_fts'); c.execute('PRAGMA user_version=9'); c.commit()
        Database(self.path).initialize()
        with self.db.transaction() as c:
            self.assertEqual([r['id'] for r in fts.search(c, ['叫团子'], CID, 'desktop', 1 << 40)], [mid])


class RecallTests(Base):
    def forget(self, content, key, scope='pet'):
        self.db.upsert_memory(content, CID, key, 'preference', 2, 1, scope=scope)
        row = next(r for r in self.db.memory_card(CID) if r['fact_key'] == key)
        self.db.forget_memories([row['id']], 'forgotten')

    def test_forgotten_memory_comes_back_when_relevant_but_not_deleted_or_private(self):
        self.forget('用户最喜欢喝冰美式咖啡', 'like:coffee')
        self.forget('用户在准备考研，目标是海洋大学', 'goal:exam', scope='private')
        self.assertEqual(self.recall.recall(CID, '今天天气真不错').archive, [])
        hit = self.recall.recall(CID, '要不要来杯冰美式')
        self.assertEqual([r['fact_key'] for r, _ in hit.archive], ['like:coffee'])
        self.assertIn('（旧事，记不太清了）用户最喜欢喝冰美式咖啡', hit.render())
        self.assertTrue(self.recall.recall(CID, '考研准备得怎么样', scene='pet').archive)
        self.assertFalse(self.recall.recall(CID, '考研准备得怎么样', scene='qq').archive)              # 私下的不在群里想起
        self.assertFalse(self.recall.recall(CID, '要不要来杯冰美式', known=['用户最喜欢喝冰美式咖啡']).archive)   # 记忆卡里已有就不重复
        # 记起来了（重新记住）就不再算旧事；用户删掉就永远不出现
        self.db.upsert_memory('用户最喜欢喝冰美式咖啡', CID, 'like:coffee')
        self.assertFalse(self.recall.recall(CID, '要不要来杯冰美式').archive)
        row = next(r for r in self.db.memory_card(CID) if r['fact_key'] == 'like:coffee')
        self.db.delete_memory(row['id'], CID)
        self.assertFalse(self.recall.recall(CID, '要不要来杯冰美式').archive)

    def test_older_chat_found_only_in_same_channel_and_after_watermark(self):
        old = self.db.add_message('user', '我下个月要去青岛看海，想去海底世界', CID)
        group = self.db.add_message('user', '群里有人说青岛海底世界很好玩', CID, channel='qq:group:1')
        window = self.fill_chat(40)
        hit = self.recall.recall(CID, '青岛海底世界怎么样', before_id=window[10])
        self.assertEqual([r['id'] for r, _ in hit.chats], [old])
        self.assertIn('对方：我下个月要去青岛看海', hit.render())
        self.assertEqual([r['id'] for r, _ in self.recall.recall(CID, '青岛海底世界怎么样', channel='qq:group:1', before_id=1 << 40).chats], [group])
        self.assertFalse(self.recall.recall(CID, '青岛海底世界怎么样', before_id=old).chats)           # 在当前窗口里的不算
        self.db.set_setting(f'context_after:{CID}', old)                                              # 删除记忆时设的水位线
        self.assertFalse(self.recall.recall(CID, '青岛海底世界怎么样', before_id=window[10]).chats)

    def test_past_tasks_only_in_private(self):
        run = self.db.add_agent_run(CID, '把桌面上的截图整理到截图文件夹'); self.db.finish_agent_run(run, 'done', '整理了 12 张')
        hit = self.recall.recall(CID, '上次整理截图那个文件夹在哪')
        self.assertEqual([r['id'] for r, _ in hit.tasks], [run]); self.assertIn('帮对方办过事，做完了', hit.render())
        self.assertFalse(self.recall.recall(CID, '上次整理截图那个文件夹在哪', scene='qq').tasks)

    def test_scoring_and_render_limits(self):
        self.assertEqual(score('今天', '今天好热'), 0)                         # 太常见的二字词不算相关
        self.assertGreaterEqual(score('冰美式', '冰美式咖啡'), 2)
        self.assertEqual(ago('2026-11-10 09:00:00', datetime(2026, 11, 11, 8)), '昨天')
        self.assertEqual(ago('bad', datetime(2026, 11, 11)), '以前')
        for i in range(5): self.forget(f'用户喜欢第{i}种冰美式咖啡做法，' + '很长的描述'*40, f'k{i}')
        text = self.recall.recall(CID, '冰美式咖啡').render(limit_chars=300)
        self.assertLessEqual(len(text), 300)


class BudgetTests(Base):
    def test_fit_drops_oldest_keeps_latest(self):
        turns = [ChatTurn('user' if i % 2 == 0 else 'assistant', '字'*1000) for i in range(30)]+[ChatTurn('user', '最新的话')]
        kept, report = budget.fit(turns, '系统'*500, 'proactive')
        self.assertEqual(kept[-1].content, '最新的话'); self.assertEqual(kept[0].role, 'user')
        self.assertLessEqual(report.total, budget.LIMITS['proactive']); self.assertGreater(report.dropped, 0)
        kept, report = budget.fit(turns, '人设'*20000, 'companion')                               # 系统提示本身就超了
        self.assertEqual([t.content for t in kept], ['最新的话']); self.assertTrue(report.over)
        self.assertIn('系统提示本身已超过预算', budget.describe(report.as_dict()))
        self.assertEqual(budget.limit_for('companion', window=8000, max_output=1000), 3000)        # 小窗口模型按窗口封顶
        self.assertEqual(budget.limit_for('companion', window=1_000_000), budget.LIMITS['companion'])  # 大窗口也不填满
        self.assertIn('还没有记录', budget.describe(None))


class IntegrationTests(Base):
    def test_turn_brings_recall_and_records_report(self):
        model = Model()
        controller = ChatController(self.db, MemoryService(self.db), model)
        controller.character = next(c for c in self.db.characters() if c.id == CID)
        self.db.add_message('user', '我家的橘猫叫团子，特别能吃', CID)
        self.fill_chat(36, text='今天第{}次摸鱼'+'。'*600)
        self.db.add_message('user', '团子最近又胖了', CID)
        list(controller.generate('t1', controller.character, CancellationToken(), '团子最近又胖了'))
        history, prompt = model.calls[0]
        self.assertIn('我家的橘猫叫团子', prompt)                                                   # 30 条之外的旧聊天被想起
        report = self.db.get_setting(f'context_report:{CID}')
        self.assertEqual(report['purpose'], 'companion'); self.assertGreater(report['sections']['recall'], 0)
        self.assertLessEqual(report['total'], report['limit'])
        self.assertGreater(report['dropped'], 0)                                                    # 历史太长，裁掉了最早的
        self.assertEqual(history[-1].content, '团子最近又胖了')
        self.assertIn('上一轮请求共', budget.describe(report))

    def test_build_still_returns_two_values(self):
        turns, prompt = ContextService(self.db, MemoryService(self.db)).build(next(c for c in self.db.characters() if c.id == CID))
        self.assertIsInstance(prompt, str)


if __name__ == '__main__':
    unittest.main()
