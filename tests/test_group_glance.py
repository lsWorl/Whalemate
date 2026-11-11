"""桌面知道 QQ 群里在聊什么，群里不知道桌面在聊什么（负责人 2026-11-11 15:46）。"""
import unittest

from test_relationship import CID, BridgeFixture


class GroupGlanceTests(BridgeFixture):
    def say(self, text, *replies, sender='20002', group='30003', name='tangle'):
        self.model.replies.extend(replies)
        return self.service.handle(dict(platform='qq', sender_id=sender, sender_name=name, group_id=group, text=text, called=True,
                                        expression_version=1, bot_account_id='90000'))

    def test_desktop_sees_group_but_group_does_not_see_desktop(self):
        from aicompanion.services.context_service import ContextService
        from aicompanion.services.memory_service import MemoryService
        self.say('深渊配队来一个', '上半：雷神 八重 久岐忍 瑶瑶')
        # 爹在 QQ 私聊（和桌面同一段相处）问起：提示词里有群里的动静，编号不给她看
        self.say('有人在和你聊天么', '有呀 群里tangle在找我要配队', sender='10001', group='', name='爹')
        prompt = self.model.prompts[-1]
        self.assertIn('QQ 群里最近的动静', prompt); self.assertIn('tangle：深渊配队来一个', prompt)
        self.assertIn('你：上半：雷神 八重 久岐忍 瑶瑶', prompt); self.assertNotIn('tangle#', prompt)
        _, desktop = ContextService(self.db, MemoryService(self.db)).build(self.character, '在吗')
        self.assertIn('深渊配队来一个', desktop)
        # 桌面这边聊的内容，群里的提示词看不到；群里的提示词也不带“群里最近的动静”这一段
        self.say('再来一套', '行')
        group_prompt = self.model.prompts[-1]
        self.assertNotIn('有人在和你聊天么', group_prompt); self.assertNotIn('QQ 群里最近的动静', group_prompt)

    def test_nothing_when_group_is_quiet_or_memories_deleted(self):
        from aicompanion.services.group_glance import glance
        self.assertEqual(glance(self.db, CID), '')
        self.say('在吗', '在')
        self.assertIn('在', glance(self.db, CID))
        last = max(r['id'] for r in self.db.recent_messages(10, CID, channel='qq:group:30003'))
        self.db.set_setting(f'context_after:{CID}', last)          # 删除记忆后的水位同样适用
        self.assertEqual(glance(self.db, CID), '')
        self.assertEqual(glance(self.db, CID, minutes=0), '')


if __name__ == '__main__':
    unittest.main()
