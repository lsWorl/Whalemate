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
        # 给模型的是本轮短编号 + 含义；完整 asset_id、路径都不进提示
        self.assertEqual(items[0].handle,'s1');self.assertIn('s1｜庆祝',prompt)
        self.assertNotIn(items[0].asset_id,prompt);self.assertNotIn(str(self.library),prompt)
        self.assertEqual(self.service.legacy_map(items)['happy'],items[0].asset_id)
    def test_unknown_and_missing_are_filtered(self):
        self.cat.scan();self.assertEqual(self.service.prepare(self.ctx,self.character),())
        aid=self.cat.rows()[0]['asset_id'];self.cat.review(aid,**{k:self.meta[k] for k in ('author','source','license')})
        self.path.unlink();self.assertEqual(self.service.prepare(self.ctx,self.character),())
    def test_role_serious_and_no_memes(self):
        self.asset()
        self.assertEqual(self.service.prepare(self.ctx,replace(DEFAULT_CHARACTER,id='other-role')),())
        self.assertEqual(self.service.prepare(replace(self.ctx,scene='emergency'),self.character,'需要帮助'),())
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

    def test_words_do_not_classify_or_trigger_expression(self):
        self.asset()
        for text in ('我失恋了','家人住院了','这个游戏角色去世的梗笑死','程序崩溃日志看一下'):
            # 语境交给同轮大脑判断；本地不把这些词直接变成“发/不发”的开关。
            self.assertTrue(self.service.prepare(self.ctx,self.character,text))

    def test_persona_is_context_not_a_tone_keyword_switch(self):
        low=self.asset(semantic='认同',tone_strength=0)
        other=self.library/'happy/high.png';other.write_bytes(png(0xff8899aa))
        high=self.cat.register(other,self.library,dict(self.meta,semantic='认同',category='happy',intents=['庆祝'],tone_strength=3))[0]
        for style in ('稳重克制','活泼夸张'):
            char=replace(self.character,speaking_style=style)
            items=self.service.prepare(self.ctx,char,'天气不错')
            self.assertEqual({c.asset_id for c in items},{low,high})
            from aicompanion.services.context_service import ContextService
            from aicompanion.services.memory_service import MemoryService
            _,prompt=ContextService(self.db,MemoryService(self.db)).build(char)
            self.assertIn(style,prompt)

    def test_current_intent_outweighs_background_persona_and_old_topic(self):
        from aicompanion.llm.base import ChatTurn
        self.asset(semantic='项目进度工作开工干活忙项目',image_text='项目进度工作开工干活忙项目')
        path=self.library/'happy/joke.png';path.write_bytes(png(0xff116688))
        joke=self.cat.register(path,self.library,dict(self.meta,semantic='玩笑调侃大笑笑死哈哈',
                    category='joke',intents=['玩笑','调侃']))[0]
        char=replace(self.character,description='项目进度工作开工干活忙项目')
        items=self.service.prepare(self.ctx,char,'哈哈笑死，这个太好笑了',
                                  [ChatTurn('assistant','项目进度工作开工干活忙项目')])
        self.assertEqual(items[0].asset_id,joke)

    def test_source_semantic_tags_and_daily_wording_retrieve_matching_image(self):
        self.asset(semantic='想向对方展示自己的东西',semantic_tags=['展示'])
        path=self.library/'happy/celebrate.png';path.write_bytes(png(0xff228811))
        matching=self.cat.register(path,self.library,dict(self.meta,semantic='露出灿烂的笑容',
                    category='开心',intents=['分享开心'],semantic_tags=['好消息','笑开花']))[0]
        items=self.service.prepare(self.ctx,self.character,'有个好消息和你分享')
        self.assertEqual(items[0].asset_id,matching)

    def test_context_candidates_keep_room_for_other_expression_intents(self):
        for n in range(12):
            p=self.library/'happy'/f'work{n}.png';p.write_bytes(png(0xff100000+n))
            self.cat.register(p,self.library,dict(self.meta,category='work'+str(n),semantic='项目工作',
                       intents=['工作'+str(n)],emotions=['忙碌']))
        p=self.library/'happy/relax.png';p.write_bytes(png(0xff112299))
        broad=self.cat.register(p,self.library,dict(self.meta,category='relax',semantic='轻轻笑一下',
                        intents=['接话'],emotions=['轻松']))[0]
        items=self.service.prepare(self.ctx,self.character,'项目工作项目工作')
        self.assertEqual(len(items),10)
        self.assertIn(broad,{x.asset_id for x in items})
