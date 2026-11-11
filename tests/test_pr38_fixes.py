"""Codex PR #38 审查（d25e9da）的四项回归：
1. 私下办事内容不能经心情原因进入群聊提示词；
2. 明确删除的事实不能经旧办事记录召回；
3. 同一轮 <mind/> 的明确判断优先于用词兜底，“不开心”不算开心；
4. 确定超过已知模型上限的请求不发送；输出预留吃光窗口时不退回默认预算。
全部用临时库、合成数据和离线模型替身。"""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.services import budget
from aicompanion.services.context_service import ContextService
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.mood_service import MoodService
from aicompanion.services.recall import RecallService
from aicompanion.storage.database import Database

CID = 'deepseek-whale'


class Model:
    def __init__(self, reply='嗯嗯'):
        self.calls = []; self.reply = reply
    def generate_reply_stream(self, history, prompt, cancellation):
        self.calls.append((list(history), prompt)); yield self.reply


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name)/'f.sqlite3'); self.db.initialize()
        self.character = next(c for c in self.db.characters() if c.id == CID)

    def tearDown(self):
        # 生成一轮后，关系分析会在后台线程里写库；等它写完再删临时目录（否则偶发“目录不为空”）
        import threading
        for thread in threading.enumerate():
            if thread.name.startswith('aicompanion-'): thread.join(5)
        self.tmp.cleanup()

    def controller(self, model):
        controller = ChatController(self.db, MemoryService(self.db), model)
        controller.character = self.character
        return controller


class PrivacyTests(Base):
    def test_private_task_goal_stays_out_of_group_prompt(self):
        MoodService(self.db).event(CID, 'achievement', reason='一起办完了：整理隐私项目QX731的资料')
        context = ContextService(self.db, MemoryService(self.db))
        _, private = context.build(self.character, '在吗')
        _, group = context.build(self.character, '在吗', channel='qq:group:42', scene='qq')
        self.assertIn('QX731', private)                                                     # 私下可以说具体原因
        self.assertNotIn('QX731', group); self.assertNotIn('整理隐私项目', group)
        self.assertIn('开心', group); self.assertIn('一起做成了事', group)                     # 心情本身可以共享，原因只说笼统的

    def test_deleted_fact_not_recalled_through_old_task(self):
        run = self.db.add_agent_run(CID, '整理目录：内部计划ZX842的资料'); self.db.finish_agent_run(run, 'done', '整理好了')
        recall = RecallService(self.db)
        self.assertTrue(recall.recall(CID, '上次整理目录的事情').tasks)                      # 删除前可以想起
        mid = self.db.add_message('user', '内部计划ZX842要保密', CID)
        self.db.upsert_memory('内部计划ZX842要保密', CID, fact_key='synthetic:zx842', source_ids=(mid,))
        row = next(r for r in self.db.memory_card(CID) if r['fact_key'] == 'synthetic:zx842')
        self.db.delete_memory(row['id'], CID)
        hit = recall.recall(CID, '上次整理目录的事情')
        self.assertFalse(hit.tasks); self.assertNotIn('ZX842', hit.render())
        _, prompt = ContextService(self.db, MemoryService(self.db)).build(self.character, '上次整理目录的事情')
        self.assertNotIn('ZX842', prompt)
        with self.db.transaction() as c:                                                    # 办事记录本身保留作审计
            self.assertEqual(c.execute('SELECT goal FROM agent_runs WHERE id=?', (run,)).fetchone()[0], '整理目录：内部计划ZX842的资料')
        later = self.db.add_agent_run(CID, '整理目录，这次是新的资料'); self.db.finish_agent_run(later, 'done', '好了')
        with self.db.transaction() as c:                                                    # 删除之后的新任务照常能想起
            c.execute("UPDATE agent_runs SET started_at=datetime('now','localtime','+1 minute') WHERE id=?", (later,))
        self.assertEqual([r['id'] for r, _ in recall.recall(CID, '上次整理目录的事情').tasks], [later])


class SecondRoundTests(Base):
    """Codex PR #38 第二轮（fbd5dfd）。"""

    def test_mood_reason_respects_deletion(self):
        # 生产链路：任务完成写 achievement 原因（同 AgentSession._done）→ 删除相关事实 → 私下提示词不再带出原因里的代号
        run = self.db.add_agent_run(CID, '整理目录：内部计划ZX842的资料'); self.db.finish_agent_run(run, 'done', '好了')
        MoodService(self.db).event(CID, 'achievement', reason='一起办完了：整理目录：内部计划ZX842')
        context = ContextService(self.db, MemoryService(self.db))
        self.assertIn('ZX842', context.build(self.character, '上次整理目录的事情')[1])
        mid = self.db.add_message('user', '内部计划ZX842要保密', CID)
        self.db.upsert_memory('内部计划ZX842要保密', CID, fact_key='synthetic:zx842', source_ids=(mid,))
        self.db.delete_memory(next(r for r in self.db.memory_card(CID) if r['fact_key'] == 'synthetic:zx842')['id'], CID)
        _, prompt = context.build(self.character, '上次整理目录的事情')
        self.assertNotIn('ZX842', prompt)
        self.assertIn('有点开心（因为一起做成了事）', prompt)                                  # 心情强度不清空，原因只说笼统的
        self.assertEqual(MoodService(self.db).recent(CID)[0]['reason'], '一起办完了：整理目录：内部计划ZX842')   # 事件记录保留
        MoodService(self.db, clock=lambda: time.time()+2).event(CID, 'praised', reason='夸她画得好', extra={'happy': 40})   # 删除之后的新原因照常具体
        self.assertIn('夸她画得好', context.build(self.character, '在吗')[1])

    def test_neutral_judgement_blocks_fallback(self):
        model = Model(); controller = self.controller(model)
        for n in range(4):
            controller.record_user_message('你不是笨蛋')
            model.reply = '<mind at="me" voice="own" tone="neutral" toward="0" feel="平静"/>嗯，我知道'
            list(controller.generate(f'n{n}', self.character, CancellationToken(), '你不是笨蛋'))
        mood = MoodService(self.db).current(CID)
        self.assertEqual(mood.levels.get('angry', 0), 0); self.assertEqual(MoodService(self.db).recent(CID), [])
        self.assertEqual(MoodService(self.db).on_mind(CID, {'tone': 'neutral', 'toward': 0}), 'neutral')
        self.assertIsNone(MoodService(self.db).on_mind(CID, {'intent': '乐意', 'willing': 0.8}))   # 只表态不评判：仍走兜底


class FallbackTests(Base):
    def run_turn(self, controller, text, reply):
        controller.record_user_message(text)
        controller_model = controller.router.fixed
        controller_model.reply = reply
        list(controller.generate(f't{len(controller_model.calls)}', self.character, CancellationToken(), text))

    def test_quote_judgement_beats_keyword_fallback(self):
        model = Model(); controller = self.controller(model)
        for _ in range(4):
            self.run_turn(controller, '他在电影里说“笨蛋”，我只是在转述', '<mind voice="quote" at="other" tone="attack"/>哈哈这台词挺凶的')
        mood = MoodService(self.db).current(CID)
        self.assertEqual(mood.levels.get('angry', 0), 0); self.assertEqual(mood.label(), 'calm')

    def test_not_happy_is_not_praise(self):
        model = Model(); controller = self.controller(model)
        for _ in range(3): self.run_turn(controller, '我今天不开心', '怎么啦，跟我说说')
        mood = MoodService(self.db).current(CID)
        self.assertEqual(mood.levels.get('happy', 0), 0)
        self.assertNotEqual(mood.label(), 'happy')

    def test_fallback_still_works_without_judgement(self):
        model = Model(); controller = self.controller(model)
        self.run_turn(controller, '抱抱', '嘿嘿')                                         # 她没写 <mind/>：用词兜底
        self.assertEqual(MoodService(self.db).current(CID).label(), 'shy')
        self.run_turn(controller, '你真是笨蛋', '<mind at="me" tone="attack" toward="-3" feel="生气"/>哼')
        self.assertEqual(MoodService(self.db).current(CID).label(), 'angry')
        kinds = [e['kind'] for e in MoodService(self.db).recent(CID)]
        self.assertEqual(kinds[:2], ['insulted', 'affection'])                              # 有判断的那轮没再叠一次兜底

    def test_fallback_rules(self):
        self.assertEqual(MoodService.fallback('我今天不开心')[0], 'worried')
        self.assertIsNone(MoodService.fallback('他说你是笨蛋'))                               # 转述不记负面
        self.assertEqual(MoodService.fallback('你是笨蛋')[0], 'teased')
        self.assertNotEqual((MoodService.fallback('我不喜欢你这样') or ('',))[0], 'affection')


class BudgetTests(Base):
    def test_limits(self):
        self.assertEqual(budget.limit_for('companion', 2048, 256), 768)
        self.assertEqual(budget.limit_for('companion', 2048, 4096), 0)                      # 不再退回 20000
        self.assertEqual(budget.hard_limit_for(2048, 256), 1792)
        self.assertIsNone(budget.hard_limit_for(None, 256))

    def test_known_window_overflow_is_not_sent(self):
        from aicompanion.services.persona_service import PersonaService
        personas = PersonaService(self.db); personas.ensure(self.character)
        big = personas.create(CID, {'title': '长', 'description': '设定'*2600, 'speaking_style': 's'})
        personas.switch(CID, big.id); self.character = next(c for c in self.db.characters() if c.id == CID)
        model = Model(); controller = self.controller(model)
        controller.router.resolve = lambda capability, cid, purpose='companion': SimpleNamespace(context_window=2048, max_output=256, label='测试模型')
        controller.record_user_message('你好')
        with self.assertRaises(budget.BudgetExceeded) as caught:
            list(controller.generate('big', self.character, CancellationToken(), '你好'))
        self.assertEqual(model.calls, [])                                                   # 没有调用模型
        self.assertIn('测试模型', str(caught.exception)); self.assertIn('精简人格', str(caught.exception))
        report = self.db.get_setting(f'context_report:{CID}')
        self.assertTrue(report['blocked']); self.assertIn('没有发送', budget.describe(report))
        self.assertEqual(self.character.description, '设定'*2600)                           # 不悄悄删人格

    def test_unknown_window_keeps_soft_behaviour(self):
        model = Model(); controller = self.controller(model)
        controller.record_user_message('你好')
        list(controller.generate('ok', self.character, CancellationToken(), '你好'))
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(self.db.get_setting(f'context_report:{CID}')['blocked'])


if __name__ == '__main__':
    unittest.main()
