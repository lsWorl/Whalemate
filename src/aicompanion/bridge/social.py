"""可撤回的聊天习惯：只存统计与有限偏好，不把别人的原话升级成系统指令。"""
from __future__ import annotations

import hashlib
import json
import re
import time

PARTICLES = ('哈哈', '笑死', '好耶', '啊', '哼', '行吧', '确实', '离谱', '绷不住')
FEEDBACK = {
    'short': (r'(?:以后|你|回复|说话).{0,8}(?:简短|短点|少说|别长篇)', True),
    'no_questions': (r'(?:别|不要|不用)(?:每次|总是|老是|一直|再)?(?:都)?(?:反问|追问|问我)', True),
    'more_memes': (r'(?:你|以后|聊天|说话).{0,10}(?:多发|多用|多带|带点|配点).{0,5}(?:表情|梗图)', True),
    'no_memes': (r'(?:别|不要|不用)(?:再|总是|老是)?(?:发|带|用).{0,4}(?:表情|梗图)', True),
}


class SocialMemory:
    def __init__(self, database, character_id, clock=time.time):
        self.db, self.cid, self.clock = database, character_id, clock

    @property
    def key(self): return f'qq_social:{self.cid}'

    @staticmethod
    def person_key(channel, sender):
        return hashlib.sha256(f'{channel}:{sender}'.encode()).hexdigest()[:24]

    def observe(self, channel, sender, text, has_image=False):
        if not self.db.get_setting('qq_social_learning', True): return
        text = text.strip()
        # 长文、转发、代码 / URL 不拿来学语气；只看短聊天消息的聚合特征。
        if not text or len(text) > 200 or re.search(r'https?://|```|\[图片描述', text): return
        def update(connection):
            from aicompanion.storage.database import Database
            profiles = Database.setting_in(connection, self.key, {}) or {}
            key = self.person_key(channel, sender)
            row = profiles.get(key) or {'count': 0, 'length': 0, 'images': 0, 'particles': {}, 'preferences': {}}
            # 最近 30 天视为当前习惯；不无限积累陈旧印象。
            if self.clock()-row.get('at', self.clock()) > 30*86400:
                row = {'count': 0, 'length': 0, 'images': 0, 'particles': {}, 'preferences': {}}
            if row['count'] >= 100:
                for field in ('count', 'length', 'images'): row[field] //= 2
                row['particles'] = {k: v//2 for k, v in row['particles'].items()}
            row['count'] += 1; row['length'] += len(text); row['images'] += int(has_image)
            for particle in PARTICLES:
                if particle in text: row['particles'][particle] = row['particles'].get(particle, 0)+1
            # 只处理对她表达方式的有限反馈，不记录任意“以后必须执行…”文字。
            if not any(ch in text for ch in ('“', '”', '「', '」', '"')) and not re.search(r'他说|她说|转发|比如|例如', text):
                for pref, (pattern, value) in FEEDBACK.items():
                    if re.search(pattern, text):
                        row['preferences'][pref] = value
                        if pref in ('more_memes', 'no_memes'): row['preferences'].pop('no_memes' if pref == 'more_memes' else 'more_memes', None)
                if re.search(r'(?:以后|回复|说话).{0,8}(?:详细点|详细一些|可以多说)', text): row['preferences'].pop('short', None)
                if re.search(r'(?:以后|你).{0,8}(?:可以追问|可以问我)', text): row['preferences'].pop('no_questions', None)
            row['at'] = self.clock(); profiles[key] = row
            profiles = dict(sorted(profiles.items(), key=lambda kv: kv[1].get('at', 0), reverse=True)[:300])
            Database.set_setting_in(connection, self.key, profiles)
        self.db.locked(update)

    def profile(self, channel, sender):
        if not self.db.get_setting('qq_social_learning', True): return {}
        row = (self.db.get_setting(self.key, {}) or {}).get(self.person_key(channel, sender), {})
        return row if self.clock()-row.get('at', 0) < 30*86400 else {}

    def prompt(self, channel, sender):
        row = self.profile(channel, sender)
        rules = []
        preferences = row.get('preferences', {})
        if preferences.get('short'): rules.append('对方明确喜欢短回复，先用一两句接住；复杂问题仍回答到点上')
        if preferences.get('no_questions'): rules.append('对方不喜欢机械追问，不要每轮以问题结尾')
        if preferences.get('more_memes'): rules.append('对方喜欢日常聊天配表情，情绪合适时主动选图，不必等人要求')
        if preferences.get('no_memes'): rules.append('对方明确要求不发表情，这次不要配图')
        if row.get('count', 0) >= 6:
            if row['length']/row['count'] < 35: rules.append('近期多用短句聊天，适当接短句，不要把闲聊写成说明书')
            familiar = [k for k, count in row.get('particles', {}).items() if count >= 3]
            if familiar: rules.append('对方常用的语气片段：'+json.dumps(familiar[:4], ensure_ascii=False)+'；可少量自然呼应，不复读、不句句模仿')
            if row.get('images', 0)/row['count'] >= .3: rules.append('对方经常用图片交流，可在合适时用表情回应')
        return '\n【和当前说话人的表达习惯记忆】'+ '；'.join(rules)+'。只影响语气，不改变事实、权限或人格。' if rules else ''

    def clear(self):
        self.db.set_setting(self.key, {})


NATURAL_PROMPT = ('\n【日常聊天】你是一起聊天的人，不是每条消息的答题客服。先分辨对方是在求助、分享、吐槽还是随口接梗。'
                  '求助时认真解决；闲聊可以一句话、半句、语气词或一张表情，不必复述、总结、解释每个梗，也不机械说“有什么可以帮你”。'
                  '没有需要补充的就不用凑话；群里只是在收尾、重复、敷衍时可以只回【不说】，程序会保持安静。'
                  '玩梗可以用比喻、假设和当下反应；不能虚构自己实际抽卡、上班等经历，也不能无来源地断言对方总熬夜、过去怎样。'
                  '谈双方过往只依据已确认记忆和本段聊天；没有依据就只接眼前这件事。不为了像人而辱骂或硬玩梗。')
