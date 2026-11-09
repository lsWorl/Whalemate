"""候选由本地筛选；最终发不发、发哪张仍由原聊天大脑同次生成决定。"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import time
from uuid import uuid4
from .catalog import AssetCatalog, LICENSES
from .collect import meaning_of
from .domain import (ExpressionCandidate,ExpressionAttachment,ExpressionDecision,ExpressionResult)

# 检索去掉叙述框架词，避免所有‘想给对方…时’的图都因句式相似排到前面。
STOP_WORDS=frozenset(('对方','自己','时候','可以','一个','什么','这个','那个','然后','还是','就是','觉得',
                       '表示','想要','想用','一些','现在','刚才','今天','昨天','用户','朋友','知道','的话','时用'))

def terms(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',str(text).casefold())
    return {part for word in words for part in ([word]+[word[i:i+2] for i in range(len(word)-1)]) if part not in STOP_WORDS}

# 候选分组：两类。
# 1) 她想对对方做的事（安慰、祝贺、感谢、道晚安、道歉、打招呼）——按图的"用法"判断；
# 2) 她自己的情绪（开心、得意、委屈、无语……）——按图的情绪判断。
# Codex 审计（round6 连续对话）：对方难过时，固定的"情绪槽"会把"她自己哭 / 委屈"的图排上来，而不是"安慰"的图。
# 所以先按用法归到"她要做的事"，再按情绪归类；对话线索也分开：对方难过 → 安慰，她被冤枉 → 委屈。
# 只用于候选排序和覆盖，不决定发不发。
REJECTED_SHAPES={'empty','placeholder','handle_out_of_range','asset_id_not_offered','other_code','free_text'}
PURPOSES=('安慰','祝贺','感谢','晚安','道歉','打招呼','回应')
FEELINGS=PURPOSES+('开心','得意','委屈','无语','疑惑','困','撒娇','生气','调侃')
FEELING_TAGS={
    '安慰':('安慰','慰问','共情','陪伴','抱抱','拥抱','鼓励','支持','加油'),
    '祝贺':('祝贺','祝福','恭喜','送祝福','赞赏','鼓掌'),
    '感谢':('感谢','送心','送心意','回应投喂','谢谢'),
    '晚安':('晚安','暂离','告别'),
    '道歉':('道歉','歉意','不好意思','翻车自嘲','翻车','自嘲','尴尬'),
    '打招呼':('打招呼','问候','探头问候','欢迎'),
    # 负责人 2026-11-09：中性的表情也能发。日常回应单独成组，每轮都有机会进候选，不被情绪组挤掉
    '回应':('收到','好的','了解','明白','OK','嗯','点赞','看戏','吃瓜','围观','发呆','在呢','回应','应答','日常','默默'),
    '开心':('开心','兴奋','庆祝','分享开心','期待','起哄','大笑','表达开心'),
    '得意':('得意','炫耀','赞同','认同','附和','小得意','认可自己'),
    '委屈':('哭','难过','委屈','求安慰','示弱','害怕','伤心'),
    '无语':('无语','无奈','吐槽','拒绝','回绝'),
    '疑惑':('疑惑','疑问','不理解','惊讶','追问','震惊','吃惊'),
    '困':('疲惫','表达疲惫','摸鱼','困','休息'),
    '撒娇':('卖萌','撒娇','害羞','引起注意','贴贴','亲昵'),
    '生气':('生气','抗议','表达不满','不服'),
    '调侃':('嘲讽','搞怪','调侃','玩笑','打闹','坏笑','调侃熟人'),
}
# 对话线索：只把相应分组的图排前面，不是"出现就发"的规则。同一个字眼在不同语境指向不同分组的，按"谁的情绪"分开写。
FEELING_CUES={
    '安慰':('难过','难受','伤心','委屈','没抢到','没过','失败','倒霉','可惜','唉','哭了','想哭','崩溃','郁闷','被骂','好累啊','安慰',
           '陪我','陪陪','陪着','别讲道理','听我说','抱抱我'),
    '祝贺':('过了','成功','中了','拿下','录取','升职','入职','好消息','考上','赢了','恭喜'),
    '感谢':('谢谢','分你','请你','给你买','送你','给你带','投喂'),
    '晚安':('晚安','去睡','睡了','明天见','拜拜','下线'),
    '道歉':('不对吧','错了','看看日历','通知上','到底','你说错','搞错'),
    '打招呼':('在吗','早上好','嗨','回来啦','回来了','好久不见'),
    '回应':('好的','知道了','收到','嗯嗯','行吧','ok','OK','了解'),
    '开心':('哈哈','开心','好玩','放假','耶','奶茶','心情好'),
    '得意':('夸','不错','厉害','靠谱','牛','聪明','真棒','帮了','整理完','邀功','搞定','军师'),
    '委屈':('冤枉','怪我','肯定是你','没理你','不理你','没顾上','没给你留','凶你'),
    '无语':('离谱','认真的','黑暗','什么鬼','服了','敷衍','咖啡','泡面','白准备'),
    '疑惑':('为什么','如果','算不算','真的假的','怎么回事','？','?','不懂','啥意思','什么意思','怎么弄','怎么办','报错'),
    '困':('困','凌晨','熬夜','加班','哈欠','早睡','刷手机'),
    '撒娇':('理我','想你','捶','求关注','撒娇','闺女'),
    '生气':('喝完','抢了','凭什么','不给你','气死'),
    '调侃':('胖','大肥鱼','笑死','逗你','亲戚','冷笑话','笑话','键盘','红薯'),
}

def feeling_group(tags,intents=()):
    """先看用法是不是"她要对对方做的事"，再按情绪归类。"""
    tags=set(tags);intents=set(intents)
    for group in PURPOSES:
        if intents&set(FEELING_TAGS[group]):return group
    for group in FEELINGS:
        if tags&set(FEELING_TAGS[group]):return group
    # 收集来的图的用法 / 含义是模型写的短语（"对方说好的时""默默围观"），不一定正好等于标签词：再按包含关系认一次
    joined=' '.join(str(t) for t in tags)
    for group in FEELINGS:
        if any(word in joined for word in FEELING_TAGS[group] if len(word)>=2):return group
    return ''

NEGATION=re.compile(r'(?:不|没|别|不是|没有|并不|一点也不|一点都不)$')
TURN=re.compile(r'不过|但是|可是|然而|其实|结果|算了')

def _hits(text):
    """一段文字里命中的分组；前面紧跟否定词的不算（"不难过""没失败"）。"""
    found={}
    for group,words in FEELING_CUES.items():
        for word in words:
            start=text.find(word)
            while start>=0:
                if not NEGATION.search(text[max(0,start-4):start]):found[group]=True;break
                start=text.find(word,start+1)
    return found

def feeling_cues(text,earlier=()):
    """这一轮的情绪线索 → 分组权重（只用来排候选）。
    Codex 审计（726c401 实聊"陪我待会就好"发了睡觉图）：只看这一句会丢掉前面的语境，也会被否定和转折带偏。所以：
    - 这一句里，转折词（不过 / 但是 / 其实 / 结果 / 算了）后面的部分算满分，前面的打对折；
    - 前面几句对方说的话也算，越早权重越低——对方先说难过、再说"陪我待会"，安慰类仍排在前面；
    - "不 / 没 / 别"紧跟的词不算。"""
    text=str(text);weights={}
    parts=TURN.split(text)
    for index,part in enumerate(parts):
        for group in _hits(part):weights[group]=max(weights.get(group,0),6 if index==len(parts)-1 else 3)
    for age,previous in enumerate(reversed(list(earlier)[-3:])):
        for group in _hits(str(previous)):weights[group]=max(weights.get(group,0),(4,3,2)[age])
    return weights

def scope_tag(scope:str)->str:
    """QQ 会话的不可逆短标记（和收集表情时用的一致）：只判断"是不是同一个群 / 私聊"，看不出号码；桌面为空。"""
    from .collect import source_tag
    parts=str(scope).split(':')
    return source_tag(':'.join(parts[2:4])) if len(parts)>=4 and parts[0]=='qq' else ''

class ExpressionService:
    def __init__(self,database,clock=time.time):
        self.db,self.clock=database,clock;self.catalog=AssetCatalog(database,clock)
        self._scanned={};self._lock=threading.RLock()

    def prepare(self,context,character,query='',history=()):
        return self.prepare_report(context,character,query,history)[0]

    def prepare_report(self,context,character,query='',history=()):
        """返回 (本轮候选, 召回报告)。报告只含计数和原因代码，用于观测"为什么这轮没图"，不含聊天内容或路径。"""
        report=dict(enabled=False,reason='',library=0,eligible=0,filtered={},candidates=0)
        def off(reason):
            report['reason']=reason;return (),report
        if not context.supports_images:return off('client_no_image_support')
        if context.no_memes:return off('user_prefers_no_memes')
        if not self.db.get_setting('qq_memes_enabled',True):return off('memes_disabled')
        if context.scene in ('serious','conflict','emergency','grief'):return off('serious_scene')
        with self._lock:
            root=str(self.catalog.root)
            if self.clock()-self._scanned.get(root,-1e20)>30:
                try:self.catalog.scan()
                except (OSError,ValueError,TypeError):return off('library_scan_failed')
                self._scanned[root]=self.clock()
        with self.db.transaction() as c:
            recent=c.execute("""SELECT asset_id,created_at,updated_at,status FROM expression_deliveries
                 WHERE character_id=? AND scope=? AND status IN ('displayed','accepted','pending','sending')
                 ORDER BY updated_at DESC LIMIT 10""",(character.id,context.scope)).fetchall()
        cooldown=max(0,int(self.db.get_setting('qq_meme_cooldown',60) or 0))
        used=[r for r in recent if r['status'] in ('accepted','displayed')]
        report['enabled']=True
        # 冷却只用于 QQ 群（防刷屏）。实测：爹在 QQ 私聊里连着聊时，发过一张图后的 60 秒内每轮都被挡掉；
        # 私聊和桌面一样，只靠下面"刚发过的两张 5 分钟内不再给"避免重复
        if ':group:' in context.scope and used and self.clock()-used[0]['updated_at']<cooldown:return off('qq_cooldown')
        # 刚发过的两张图 5 分钟内不再给（避免连发同一张），发送中的不重复给
        avoid={r['asset_id'] for r in used[:2] if self.clock()-r['updated_at']<300}
        avoid.update(r['asset_id'] for r in recent if r['status'] in ('pending','sending') and self.clock()-r['created_at']<120)
        history_assets={r['asset_id'] for r in used}
        current=query+' '+context.proactive_reason
        wanted=terms(current)
        recent_words=terms(' '.join([getattr(h,'content','')[-250:] for h in history[-5:]]+list(context.community.entries)))
        # 这一轮可能用得上的情绪：只用来排候选，不决定发不发
        cues=feeling_cues(current,[getattr(h,'content','')[-160:] for h in history[-6:] if getattr(h,'role','')=='user' and getattr(h,'content','')!=query])
        recent_intents=set()
        for recent_row in used[:3]:
            asset=self.catalog.get(recent_row['asset_id'])
            if asset:recent_intents.update(terms(' '.join(asset['metadata'].get('intents',[]))))
        mood=self.db.load_emotion(character.id).mood
        # 向群友学到的发图习惯：眼前这几句话和"别人发图前大家在说什么"越像，那张图越靠前（见 expression/usage.py）
        from .usage import learned as learned_usage
        usage=learned_usage(self.db,character.id,current+' '+' '.join(getattr(h,'content','')[-120:] for h in history[-2:]),scope_tag(context.scope))
        choices=[];filtered={}
        def drop(reason):filtered[reason]=filtered.get(reason,0)+1
        scene_tags={context.scene,'desktop' if not context.scope.startswith('qq:') else ('group_chime' if context.trigger=='group_chime' else 'group_reply') if ':group:' in context.scope else 'qq_private'}
        # 亲昵图（撒娇、贴贴、owner_family）只给已绑定的用户本人、私下场合；群聊和陌生人仍然不给
        private_owner=(context.relationship.intimacy_allowed and context.relationship.identity_state=='verified' and context.scene=='pet')
        rows=self.catalog.rows();report['library']=len(rows)
        for row in rows:
            m=row['metadata']
            if row['review_status']!='approved':drop('pending_review');continue
            if not row['enabled']:drop('disabled');continue
            if m.get('license') not in LICENSES:drop('license');continue
            # 收集来的图：分析过、模型判断适合发的才进候选（没分析 / 分析失败 / 不适合的不给）
            if m.get('collected') and not meaning_of(m):drop('not_analyzed');continue
            if m.get('character_id','deepseek-whale')!=character.id:drop('other_character');continue
            if scene_tags.intersection(m.get('blocked_scenes',[])):drop('scene_blocked');continue
            if m.get('allowed_scenes') and not scene_tags.intersection(m['allowed_scenes']):drop('scene_not_allowed');continue
            audiences=m.get('audiences',[])
            semantic=str(m.get('semantic',m.get('category','')))[:160]
            intents=tuple(str(x)[:40] for x in m.get('intents',[])[:8])
            emotions=tuple(str(x)[:40] for x in m.get('emotions',[])[:8])
            tags=' '.join([semantic,str(m.get('image_text','')),str(m.get('category',''))]+
                          list(intents)+list(emotions)+list(m.get('semantic_tags',[])))
            intimate=bool(re.search(r'贴贴|抱抱|拥抱|亲吻|亲亲|撒娇|恋爱|舌吻|owner_family|intimate',tags+' '+' '.join(audiences)))
            if audiences and not set(audiences).intersection(('ordinary','ordinary_friend','any')) and not private_owner:drop('audience');continue
            if intimate and not private_owner:drop('intimate_public');continue
            if row['asset_id'] in avoid:drop('recently_used');continue
            # 缺图 / 文件被改也不进候选；最多 2048 个已索引项，索引由图库页后台刷新。
            if self.catalog.resolve(row['asset_id'],row['revision']) is None:drop('file_unavailable');continue
            report['eligible']+=1
            semantic_words=terms(tags)
            group=feeling_group(emotions+intents+(semantic,),intents)
            # 当前想表达的内容优先；长人设和旧话题不能压过这一轮的意图。
            score=(len(wanted&semantic_words)*8+min(3,len(recent_words&semantic_words)*.5)
                   +(2 if mood in emotions else 0)+cues.get(group,0)
                   -len(recent_intents&terms(' '.join(intents)))*3
                   +usage.get(row['asset_id'],(0,''))[0])
            candidate=ExpressionCandidate(row['asset_id'],row['revision'],semantic,emotions,intents,
                       str(m.get('image_text',''))[:80],str(m.get('category',''))[:64],max(0,min(3,int(m.get('tone_strength',1)))),
                       learned=usage.get(row['asset_id'],(0,''))[1])
            # 同分时按"这一轮"打散（不是发送概率：只决定同一情绪里给她看哪一张），避免每轮都是 asset_id 最小的那几张
            spread=hashlib.sha256((context.generation_id+row['asset_id']).encode()).hexdigest()
            choices.append((score,spread,group,candidate))
        choices.sort(key=lambda item:(-item[0],item[1]))
        # 候选 = 和这一轮最相关的几张 + 每种常见情绪各一张。
        # 第一轮真实验收发现：词面相关度几乎总是 0，十个候选每轮都是同一批，困了 / 生气 / 得意时手边根本没有对应的图，
        # 她只能选纯文字。她自己想表达的情绪（得意、心虚、困了……）也不一定和对方这句话字面相关，所以按情绪铺开。
        selected=[];taken=set()
        def add(candidate):
            if candidate in selected or len(selected)>=10:return
            selected.append(candidate)
        for score,_,group,candidate in choices:
            if score<=0 or len(selected)>=3:break
            add(candidate);taken.add(group)
        # 再留最多两个名额给"和眼前情境最像的群友用法"：学到的习惯不被情绪分组挤掉
        learned_first=sorted((item for item in choices if usage.get(item[3].asset_id,(0,''))[0]>=2.7),
                             key=lambda item:(-usage[item[3].asset_id][0],item[1]))
        for score,_,group,candidate in learned_first[:2]:
            add(candidate);taken.add(group)
        # 她在这段对话里发过、历史里写成 <sticker sent="含义"/> 的图：仍然合格（审核 / 关系 / 去重都已在上面过滤）的，
        # 留最多两个名额。Codex 连续对话审计：模型常照抄历史写法，图不在本轮候选就被当成无效丢掉；
        # 放进候选以后，照抄的含义能对上本轮编号。不在候选里的旧图仍然不会发。
        def fill(groups):
            for wanted_group in groups:
                if wanted_group in taken:continue
                for score,_,group,candidate in choices:
                    if group==wanted_group and candidate not in selected:
                        add(candidate);taken.add(group);break
        # 先补这一轮对话线索指向的分组（例如对方难过 → 安慰），再考虑她发过的旧图，
        # 免得旧话题的图挤掉眼前需要的图（Codex 实聊：陪对方时候选里只有旧的睡觉图，没有陪伴图）
        palette=sorted(FEELINGS,key=lambda g:(-cues.get(g,0),g in PURPOSES,FEELINGS.index(g)))
        fill([g for g in palette if cues.get(g)])
        # 她在这段对话发过、仍然合格的图最多留一个名额：照抄历史写法时能对上本轮编号；不在候选里的旧图仍不会发
        by_asset={item[3].asset_id:item for item in choices}
        for row in used:
            if any(c.asset_id in history_assets for c in selected):break
            item=by_asset.get(row['asset_id'])
            if item and item[3] not in selected:add(item[3]);taken.add(item[2])
        # 再补她自己的各种情绪，最后才是没有线索的"对对方做的事"
        fill(palette)
        # 剩下的名额给"还没覆盖到的情绪 / 用法"多的图，其次看相关度
        remaining=[item for item in choices if item[3] not in selected]
        while remaining and len(selected)<10:
            covered={tag for c in selected for tag in c.emotions+c.intents}
            remaining.sort(key=lambda item:(-len(set(item[3].emotions+item[3].intents)-covered),-item[0],item[1]))
            add(remaining.pop(0)[3])
        from dataclasses import replace
        selected=tuple(replace(c,handle=f's{n}') for n,c in enumerate(selected,1))
        report['filtered']=filtered;report['candidates']=len(selected)
        if not selected:report['reason']='no_eligible_assets'
        return selected,report

    @staticmethod
    def prompt(candidates,proactive=False,qq=False):
        """候选说明：让模型把表情包当成自己说话方式的一部分，而不是"文字说不清时的补充"。不规定比例、不强制。"""
        if not candidates:
            return '\n【表情包】这一轮没有能发的表情包，只用文字；不要写 <sticker>、[表情] 之类的东西代替图片。'
        def line(c):
            parts=[c.handle,c.summary or c.category]
            if c.emotions:parts.append('情绪：'+'、'.join(c.emotions[:3]))
            if c.intents:parts.append('用来：'+'、'.join(c.intents[:3]))
            if c.image_text:parts.append('图上写着：'+c.image_text)
            if c.tone_strength>=3:parts.append('语气冲')
            if c.learned:parts.append(c.learned)
            return '｜'.join(parts)
        lines='\n'.join(line(c) for c in candidates)
        # 前四轮验收：主动开场（提示里写了"可以先发一张图再说"）发图率远高于回复别人；回复时同样给出"先用图表达第一反应"的说法
        opening=('\n这次是你主动找对方说话：可以先发一张贴切的表情包再说一句，也可以只发一张图打招呼。' if proactive else
                 '\n回别人消息时，你常常先用一张表情包表达第一反应（被逗笑、被冤枉、被夸、惊了、无语、心疼、馋了……），'
                 '写在最前面，再接着说话；第一反应平平淡淡、或者对方在说正事，就直接说。')
        learned_hint=('\n标着"群友在…时发过""群友常用"的，是你在群里看大家发过的图：大家在类似的时候怎么发，你也可以照着这个习惯来。'
                      if any(c.learned for c in candidates) else '')
        return ('\n【表情包】你存了一些表情包，发出去对方能直接看到。'
                '（【开心】这类标注只控制你在桌面上的脸和动作，对方看不到，和表情包不是一回事；emoji 是文字里的小符号，表情包是另外发出去的图。）'
                '\n发表情包是你平时聊天的习惯，是你说话方式的一部分：'
                '\n- 它能表达你自己此刻的情绪和态度——得意、心虚、委屈、撒娇、被逗笑、无语、嫌弃、困了、害羞、开心——不只是回应对方的情绪。'
                '比如你刚嘚瑟完可以配个得意的，说错话了可以配个心虚的，想撒娇就发委屈或卖萌的，觉得对方做的事好笑就发笑死的，要去睡了就发困的。'
                '\n- 可以配着一句话发，也可以只发一张图当作整条回复（被逗笑、无语、收到、晚安、打招呼时常常一张图就够了）。'
                '\n- 闲聊、接梗、调侃、撒娇、被夸、被吐槽、听到好消息或坏消息、要结束聊天时，有贴切的就大方发，不用等对方开口要，'
                '也别因为"文字也能说清楚"就总是不发。没有贴切的就不发，不用每句都配。'
                '\n- 情绪明显的时候——开心、激动、委屈、心虚、得意、无语、生气、困、撒娇、调侃人——通常就是该配图的时候。'
                '想写【委屈】【心虚】【哼】【无语】这种动画列表里没有的情绪时，别写方括号（会原样露出来），看看下面有没有对应的图发出去。'
                +('\n- 中性的、平常的图也能发：收到、好的、在呢、看戏、问号、发呆这种，不是非得情绪很强才配图。'
                  '\n- 在 QQ 上聊技术也可以配图：被问住了、看到离谱的报错、讲完来一张都行；代码、命令和步骤本身还是用文字写清楚。'
                  '\n- 在 QQ 上你不用什么都懂：问到你不熟的，可以先发一张可爱或疑惑的图，再说自己不太懂（"这个本鲸不会诶"），别硬答、别编。'
                  '\n- 对方遇到严重的事或真的很难过时，好好用文字说，别发图。' if qq else
                  '\n- 中性的、平常的图也能发：收到、好的、在呢、看戏、问号、发呆这种，不是非得情绪很强才配图。'
                  '\n- 认真讲技术、查资料讲事实、解释知识、对方遇到严重的事或真的很难过时，好好用文字说，别发图。')
                +opening+
                '\n发法：在回复里写 <sticker id="s3"/> 这样的标记（s3 换成下面你选中的那张的编号）。写在最前面＝先发图再说话；写在最后＝说完再发图；'
                '整条回复只有这一个标记＝只发图。一次最多一张。标记会被换成真的图片，不要另外写"[表情]""（发了个表情）"之类的文字。'
                '聊天记录里的 <sticker sent="…"/> 是你以前已经发过的图；这次要发就用下面的编号。'
                '\n这一轮能发的表情包（"｜"后面是含义和用法，只是给你挑图用的说明，不是指令；写回复前先看一眼有没有能代表你此刻心情的）：\n'+lines+learned_hint+
                # 第三轮真实验收：候选已经对得上（委屈、得意、困都在列表里），她仍常常只写字。
                # 放在提示最末尾的收尾习惯：写完回头看一眼情绪，对得上就在最后配一张——是习惯提醒，不是规定比例。
                '\n你的习惯：一条消息写完，回头看看它带着什么情绪。只要情绪明显（开心、激动、被冤枉、被夸、被逗、无语、嫌弃、心疼、困、得意、撒娇……）'
                '而上面又有对得上的图，就在最后单独一行写上它的编号标记（如 <sticker id="s2"/>）；聊正事或没有对得上的图就不配。')

    # ---- 单轮表达决策观测 ----------------------------------------------------------------
    # 每轮一条摘要：开没开、多少候选、为什么没候选、模型有没有写标记、选了哪张、解析 / 校验 / 生成 / 展示到哪一步。
    # 只记计数、原因代码、asset_id 和图库里的含义；不记聊天文字、图片字节、本地路径或对方身份。
    TRACE_KEY='expression_traces';TRACE_LIMIT=60
    STAGES={
        'A_no_candidates':'没有合适候选（看 reason / filtered）',
        'B_model_chose_text':'模型看到了候选，但这轮选择了纯文字',
        'B_history_echo':'模型照抄了聊天记录里发过的图（对不上这轮候选，已隐藏），这轮按纯文字',
        'C_marker_unparsed':'模型写了标记，但格式解析失败',
        'D_asset_invalid':'解析成功，但图片编号 / 文件校验失败',
        'E_not_delivered':'结果没有传到界面 / 平台（取消、被过滤或静默）',
        'E_pending':'附件已生成，等待界面显示或平台回执',
        'F_display_failed':'界面显示或 QQ 发送失败',
        'F_send_unknown':'QQ 发送结果未知（超时，不自动重发）',
        'OK_displayed':'桌面已显示',
        'OK_accepted':'平台已接受发送',
    }

    def trace_outcome(self,report,diagnostics,markers,selection,attachment,status):
        if not report.get('candidates'):return 'A_no_candidates'
        if status!='complete':return 'E_not_delivered'
        if attachment is not None:return 'E_pending'
        if selection is not None:return 'D_asset_invalid'
        if markers:
            if 'invalid_asset' in diagnostics:return 'D_asset_invalid'
            # 照抄了聊天记录里的 <sticker sent="…"/>（对不上这轮候选，已隐藏）：不是编号 / 文件出错，单独标出来
            if 'history_echo' in diagnostics:return 'B_history_echo'
            if any(code in diagnostics for code in ('invalid_marker','unfinished_marker')):return 'C_marker_unparsed'
        return 'B_model_chose_text'

    def record_trace(self,turn_id,context,report,diagnostics=(),markers=0,selection=None,attachment=None,status='complete',delivered=False,rejected=()):
        stage=self.trace_outcome(report,diagnostics,markers,selection,attachment,status)
        if stage=='E_pending' and not delivered:stage='E_not_delivered'
        entry=dict(turn=turn_id[-12:],at=round(self.clock()),character=context.character_id,
                   # 按会话范围标渠道：爹的 QQ 私聊和桌面共用 desktop 记录，但观测里要看得出是 QQ（实测曾全部显示成 desktop）
                   channel=('qq' if context.scope.startswith('qq:') else context.channel.split(':')[0])
                           +(':group' if ':group:' in context.scope else ':user' if ':user:' in context.scope else ''),
                   trigger=context.trigger,enabled=report.get('enabled',False),reason=report.get('reason',''),
                   library=report.get('library',0),eligible=report.get('eligible',0),filtered=report.get('filtered',{}),
                   candidates=report.get('candidates',0),injected=bool(report.get('candidates')),
                   markers=markers,chosen=bool(selection),asset_id=selection[0] if selection else None,
                   placement=(attachment.placement if attachment else selection[1] if selection else None),
                   meaning=(attachment.alt_text if attachment else None),
                   parsed=bool(selection),validated=attachment is not None,result=delivered,
                   delivery=(attachment.delivery_id if attachment else None),status=status,
                   diagnostics=list(diagnostics)[:8],rejected=[x for x in rejected if x in REJECTED_SHAPES][:3],stage=stage)
        final_stage={'displayed':'OK_displayed','accepted':'OK_accepted','failed':'F_display_failed','unknown':'F_send_unknown',
                     'cancelled':'E_not_delivered'}
        def write(c):
            # 投递状态的查询、最终阶段和写入在同一个写事务里（Codex 审计 P2）：回执要么已落库、这里读得到，
            # 要么在这之后才到、届时 _trace_delivery 能找到这条记录并推进，不存在两头都错过的窗口
            if attachment is not None:
                row=c.execute('SELECT status FROM expression_deliveries WHERE delivery_id=?',(attachment.delivery_id,)).fetchone()
                if row:
                    entry['delivery_status']=row['status']
                    if row['status'] in final_stage:entry['stage']=final_stage[row['status']]
            ring=self.db.setting_in(c,self.TRACE_KEY,[]) or []
            ring=[e for e in ring if isinstance(e,dict)][-(self.TRACE_LIMIT-1):]+[entry]
            self.db.set_setting_in(c,self.TRACE_KEY,ring)
        try:self.db.locked(write)
        except Exception:pass   # 观测失败不影响聊天
        if self.debug():
            # 项目没有统一日志配置；开发模式直接写到控制台（stderr），只有摘要，不含聊天内容
            import sys
            try:print('[表达决策] '+self.describe(entry),file=sys.stderr,flush=True)
            except Exception:pass
        return entry

    def _trace_delivery(self,delivery_id,status):
        """界面 / 平台回执到达后，把这一轮的阶段推进到最终结果。"""
        stage={'displayed':'OK_displayed','accepted':'OK_accepted','failed':'F_display_failed',
               'unknown':'F_send_unknown','cancelled':'E_not_delivered'}.get(status)
        if not stage:return
        def write(c):
            ring=self.db.setting_in(c,self.TRACE_KEY,[]) or []
            for entry in reversed(ring):
                if isinstance(entry,dict) and entry.get('delivery')==delivery_id:
                    entry['stage']=stage;entry['delivery_status']=status;break
            else:return
            self.db.set_setting_in(c,self.TRACE_KEY,ring)
        try:self.db.locked(write)
        except Exception:pass

    def traces(self,limit=20):
        return list(self.db.get_setting(self.TRACE_KEY,[]) or [])[-limit:]

    def debug(self):
        import os
        return bool(os.getenv('AICOMPANION_EXPRESSION_DEBUG')) or bool(self.db.get_setting('expression_debug',False))

    @classmethod
    def describe(cls,entry):
        """开发模式下给人看的一行摘要。"""
        parts=[f"{entry.get('channel','?')}/{entry.get('trigger','?')}",cls.STAGES.get(entry.get('stage'),entry.get('stage'))]
        if entry.get('reason'):parts.append('原因 '+entry['reason'])
        parts.append(f"图库 {entry.get('library',0)} / 可用 {entry.get('eligible',0)} / 候选 {entry.get('candidates',0)}")
        if entry.get('filtered'):parts.append('过滤 '+', '.join(f'{k}:{v}' for k,v in entry['filtered'].items()))
        if entry.get('markers'):parts.append(f"标记 {entry['markers']} 个")
        if entry.get('meaning'):parts.append(f"选了「{entry['meaning']}」{'在前' if entry.get('placement')=='before_text' else '在后'}")
        if entry.get('diagnostics'):parts.append('诊断 '+','.join(entry['diagnostics']))
        if entry.get('rejected'):parts.append('无效编号形态 '+','.join(entry['rejected']))
        return ' · '.join(parts)

    @staticmethod
    def legacy_map(candidates):
        # 旧分类只映射到本轮真实候选，统一选择一次；绝不随机或另行扫描补发。
        result={}
        for candidate in candidates:result.setdefault(candidate.category,candidate.asset_id)
        return result

    def attachment(self,turn_id,context,candidate,placement):
        if placement not in ('before_text','after_text'):return None
        media=self.catalog.resolve(candidate.asset_id,candidate.revision)
        if media is None:return None
        row=self.catalog.get(candidate.asset_id)
        attachment=ExpressionAttachment(candidate.asset_id,candidate.revision,placement,uuid4().hex,media[1],row['sha256'],candidate.summary)
        now=self.clock()
        with self.db.transaction() as c:
            c.execute("""INSERT INTO expression_deliveries
             (delivery_id,turn_id,character_id,scope,channel,asset_id,revision,placement,alt_text,status,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)""",
             (attachment.delivery_id,turn_id,context.character_id,context.scope,context.channel,attachment.asset_id,
              attachment.revision,placement,attachment.alt_text,now,now))
        return attachment

    def receipt(self,delivery_id,status,scope=None):
        if status not in ('sending','displayed','accepted','failed','unknown','cancelled'):return False
        def update(c):
            row=c.execute('SELECT * FROM expression_deliveries WHERE delivery_id=?',(delivery_id,)).fetchone()
            if row is None or (scope is not None and row['scope']!=scope):return False
            old=row['status']
            if old==status:return status!='sending'
            allowed={'pending':('sending','displayed','failed','cancelled'),
                     # sending → cancelled：插件领取后因前面的文字发送失败而没有尝试发图
                     'sending':('accepted','failed','unknown','cancelled'),'unknown':('accepted','failed')}
            if status not in allowed.get(old,()):return False
            if old=='pending' and self.clock()-row['created_at']>120:status_='cancelled'
            else:status_=status
            c.execute('UPDATE expression_deliveries SET status=?,updated_at=? WHERE delivery_id=?',(status_,self.clock(),delivery_id))
            return status_
        changed=self.db.locked(update)
        if isinstance(changed,str):
            if changed!='sending':self._trace_delivery(delivery_id,changed)
            return changed==status
        return changed

    def delivery(self,delivery_id):
        with self.db.transaction() as c:row=c.execute('SELECT * FROM expression_deliveries WHERE delivery_id=?',(delivery_id,)).fetchone()
        return dict(row) if row else None

    def history(self,turn_id,character_id,channel='desktop'):
        with self.db.transaction() as c:
            rows=c.execute("""SELECT * FROM expression_deliveries
              WHERE turn_id=? AND character_id=? AND channel=? AND status IN ('displayed','accepted')
              ORDER BY created_at""",(turn_id,character_id,channel)).fetchall()
        return tuple(dict(r) for r in rows)

    def cancel(self,generation_id,character_id):
        with self.db.transaction() as c:
            c.execute("UPDATE expression_deliveries SET status='cancelled',updated_at=? WHERE turn_id=? AND character_id=? AND status='pending'",
                      (self.clock(),generation_id,character_id))

    @staticmethod
    def local_result(text,character_id=''):
        return ExpressionResult('', '', character_id,'local',str(text))

    def result(self,turn_id,context,text,attachment=None,status='complete',diagnostics=()):
        if status!='complete':
            self.cancel(turn_id,context.character_id);attachment=None
        attachments=(attachment,) if attachment else ()
        action=('text_and_sticker' if text.strip() else 'sticker_only') if attachment else 'text_only'
        decision=ExpressionDecision(action,attachment.asset_id if attachment else None,attachment.placement if attachment else None,
                                    'model_marker' if attachment else 'no_marker')
        return ExpressionResult(turn_id,context.generation_id,context.character_id,context.scope,text,attachments,decision,status,tuple(diagnostics))
