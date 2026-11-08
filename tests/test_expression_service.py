"""同次生成之外只有本地候选，不做关系评分或额外模型请求。"""
from dataclasses import replace
import unittest
import test_expression_catalog as fixtures
png=fixtures.png
from aicompanion.expression.service import ExpressionService
from aicompanion.expression.domain import ExpressionContext,CommunityContext
from aicompanion.domain.character import DEFAULT_CHARACTER

class ServiceTests(unittest.TestCase):
    tearDown=fixtures.CatalogTests.tearDown
    def setUp(self):
        fixtures.CatalogTests.setUp(self)
        self.character=replace(DEFAULT_CHARACTER,id='deepseek-whale',name='鲸娘')
        self.db.add_character(self.character);self.now=1000
        self.service=ExpressionService(self.db,lambda:self.now)
        self.ctx=ExpressionContext('g','deepseek-whale','desktop:deepseek-whale')
    def asset(self,**changes):
        return self.cat.register(self.path,self.library,dict(self.meta,category='happy',intents=['庆祝'],**changes))[0]
    def test_candidates_are_real_approved_and_no_paths_in_prompt(self):
        self.asset();items=self.service.prepare(self.ctx,self.character,'终于完成项目啦')
        self.assertEqual(len(items),1);prompt=self.service.prompt(items)
        self.assertIn(items[0].asset_id,prompt);self.assertNotIn(str(self.library),prompt)
        self.assertEqual(self.service.legacy_map(items)['happy'],items[0].asset_id)
    def test_unknown_and_missing_are_filtered(self):
        self.cat.scan();self.assertEqual(self.service.prepare(self.ctx,self.character),())
        aid=self.cat.rows()[0]['asset_id'];self.cat.review(aid,**{k:self.meta[k] for k in ('author','source','license')})
        self.path.unlink();self.assertEqual(self.service.prepare(self.ctx,self.character),())
    def test_role_serious_and_no_memes(self):
        self.asset()
        self.assertEqual(self.service.prepare(self.ctx,DEFAULT_CHARACTER),())
        self.assertEqual(self.service.prepare(self.ctx,self.character,'遇到紧急情况'),())
        self.assertEqual(self.service.prepare(replace(self.ctx,no_memes=True),self.character),())
    def test_unknown_relation_does_not_get_intimate(self):
        self.asset(semantic='撒娇抱抱')
        self.assertEqual(self.service.prepare(self.ctx,self.character),())
    def test_success_usage_is_scoped_pending_does_not_count(self):
        self.asset();candidate=self.service.prepare(self.ctx,self.character)[0]
        att=self.service.attachment('g',self.ctx,candidate,'after_text')
        self.assertEqual(self.service.prepare(self.ctx,self.character),())
        self.assertFalse(self.service.history('g',self.character.id))
        self.assertTrue(self.service.receipt(att.delivery_id,'displayed',self.ctx.scope))
        self.assertEqual(self.service.prepare(self.ctx,self.character),())
        other=replace(self.ctx,scope='qq:group:other')
        self.assertEqual(len(self.service.prepare(other,self.character)),1)
        self.now+=301;self.assertEqual(len(self.service.prepare(self.ctx,self.character)),1)
    def test_receipt_idempotence_cancel_and_restart_unknown(self):
        self.asset();candidate=self.service.prepare(self.ctx,self.character)[0]
        att=self.service.attachment('g',self.ctx,candidate,'before_text')
        self.assertFalse(self.service.receipt(att.delivery_id,'sending','wrong'))
        self.assertTrue(self.service.receipt(att.delivery_id,'sending',self.ctx.scope))
        self.assertFalse(self.service.receipt(att.delivery_id,'sending',self.ctx.scope))
        self.db.initialize()
        self.assertEqual(self.service.delivery(att.delivery_id)['status'],'unknown')
        self.assertFalse(self.service.receipt(att.delivery_id,'sending'))
    def test_limit_ten_and_local_result(self):
        for n in range(14):
            p=self.library/'happy'/f'{n}.png';p.write_bytes(png(0xff000000+n))
            self.cat.register(p,self.library,dict(self.meta,category=str(n),intents=[str(n)]))
        self.assertLessEqual(len(self.service.prepare(self.ctx,self.character)),10)
        result=self.service.local_result('戳我呀','deepseek-whale')
        self.assertEqual(result.decision.action,'text_only');self.assertFalse(result.attachments)

    def test_sad_and_serious_requests_do_not_receive_joke_images(self):
        self.asset()
        for text in ('我失恋了','家人住院了','今天被裁员了','真的很难受'):
            self.assertEqual(self.service.prepare(self.ctx,self.character,text),())

    def test_persona_changes_candidate_tone_without_changing_identity(self):
        low=self.asset(semantic='认同',tone_strength=0)
        other=self.library/'happy/high.png';other.write_bytes(png(0xff8899aa))
        high=self.cat.register(other,self.library,dict(self.meta,semantic='认同',category='happy',intents=['庆祝'],tone_strength=3))[0]
        calm=replace(self.character,speaking_style='稳重克制')
        lively=replace(self.character,speaking_style='活泼夸张')
        self.assertEqual(self.service.prepare(self.ctx,calm,'天气不错')[0].asset_id,low)
        self.assertEqual(self.service.prepare(self.ctx,lively,'天气不错')[0].asset_id,high)
