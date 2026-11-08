"""候选由本地筛选；最终发不发、发哪张仍由原聊天大脑同次生成决定。"""
from __future__ import annotations
import hashlib
import json
import re
import threading
import time
from uuid import uuid4
from .catalog import AssetCatalog, LICENSES
from .domain import (ExpressionCandidate,ExpressionAttachment,ExpressionDecision,ExpressionResult)

# 检索去掉叙述框架词，避免所有‘想给对方…时’的图都因句式相似排到前面。
STOP_WORDS=frozenset(('对方','自己','时候','可以','一个','什么','这个','那个','然后','还是','就是','觉得',
                       '表示','想要','想用','一些','现在','刚才','今天','昨天','用户','朋友','知道','的话','时用'))

def terms(text):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',str(text).casefold())
    return {part for word in words for part in ([word]+[word[i:i+2] for i in range(len(word)-1)]) if part not in STOP_WORDS}

# 常见情绪分组：候选按组铺开，保证她想表达自己的情绪时手边有图。只用于排序和覆盖，不触发发送。
FEELINGS=('开心','得意','委屈','无语','疑惑','困','撒娇','生气','调侃','打招呼')
FEELING_TAGS={
    '开心':('开心','兴奋','庆祝','分享开心','期待','起哄'),
    '得意':('得意','炫耀','赞同','认同','附和'),
    '委屈':('哭','难过','委屈','求安慰','示弱','害怕'),
    '无语':('无语','无奈','吐槽','拒绝','回绝'),
    '疑惑':('疑惑','疑问','不理解','惊讶','追问'),
    '困':('疲惫','表达疲惫','摸鱼','困'),
    '撒娇':('卖萌','撒娇','害羞','引起注意'),
    '生气':('生气','抗议'),
    '调侃':('嘲讽','搞怪','调侃','玩笑'),
    '打招呼':('打招呼','问候'),
}
# 对话里出现这些字眼时，相应情绪的图排前面（排序用的提示，不是"出现就发"的规则）
FEELING_CUES={
    '开心':('哈哈','开心','好消息','中了','过了','成功','太好','放假','耶','奶茶','好玩','心情'),
    '得意':('夸','不错','厉害','靠谱','牛','聪明','真棒','帮了','整理完','邀功','搞定'),
    '委屈':('冤枉','怪我','是你','没理','不理','忘了','没抢到','难受','可惜','唉','凶','没给你留'),
    '无语':('离谱','认真的','黑暗','什么鬼','服了','敷衍','又','咖啡','泡面'),
    '疑惑':('为什么','如果','算不算','真的假的','怎么','？','?','到底','几点'),
    '困':('困','睡','晚安','凌晨','熬夜','累','加班','哈欠','早睡'),
    '撒娇':('理我','陪','想你','捶','抱','求关注','没顾上','撒娇'),
    '生气':('喝完','抢','凭什么','不给','气','坏'),
    '调侃':('胖','大肥鱼','笑死','逗','亲戚','冷笑话','笑话','刷手机','键盘'),
    '打招呼':('在吗','早','嗨','回来','打招呼','好久'),
}

def feeling_group(tags):
    for group in FEELINGS:
        if set(tags)&set(FEELING_TAGS[group]):return group
    return ''

def feeling_cues(text):
    text=str(text)
    return {group:6 for group,words in FEELING_CUES.items() if any(w in text for w in words)}

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
        if context.scope.startswith('qq:') and used and self.clock()-used[0]['updated_at']<cooldown:return off('qq_cooldown')
        # 刚发过的两张图 5 分钟内不再给（避免连发同一张），发送中的不重复给
        avoid={r['asset_id'] for r in used[:2] if self.clock()-r['updated_at']<300}
        avoid.update(r['asset_id'] for r in recent if r['status'] in ('pending','sending') and self.clock()-r['created_at']<120)
        current=query+' '+context.proactive_reason
        wanted=terms(current)
        recent_words=terms(' '.join([getattr(h,'content','')[-250:] for h in history[-5:]]+list(context.community.entries)))
        # 这一轮可能用得上的情绪：只用来排候选，不决定发不发
        cues=feeling_cues(current+' '+' '.join(getattr(h,'content','')[-120:] for h in history[-2:]))
        recent_intents=set()
        for recent_row in used[:3]:
            asset=self.catalog.get(recent_row['asset_id'])
            if asset:recent_intents.update(terms(' '.join(asset['metadata'].get('intents',[]))))
        mood=self.db.load_emotion(character.id).mood
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
            group=feeling_group(emotions+intents)
            # 当前想表达的内容优先；长人设和旧话题不能压过这一轮的意图。
            score=(len(wanted&semantic_words)*8+min(3,len(recent_words&semantic_words)*.5)
                   +(2 if mood in emotions else 0)+cues.get(group,0)
                   -len(recent_intents&terms(' '.join(intents)))*3)
            candidate=ExpressionCandidate(row['asset_id'],row['revision'],semantic,emotions,intents,
                       str(m.get('image_text',''))[:80],str(m.get('category',''))[:64],max(0,min(3,int(m.get('tone_strength',1)))))
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
        palette=sorted(FEELINGS,key=lambda g:(-cues.get(g,0),FEELINGS.index(g)))
        for wanted_group in palette:
            if wanted_group in taken:continue
            for score,_,group,candidate in choices:
                if group==wanted_group and candidate not in selected:
                    add(candidate);taken.add(group);break
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
    def prompt(candidates,proactive=False):
        """候选说明：让模型把表情包当成自己说话方式的一部分，而不是"文字说不清时的补充"。不规定比例、不强制。"""
        if not candidates:
            return '\n【表情包】这一轮没有能发的表情包，只用文字；不要写 <sticker>、[表情] 之类的东西代替图片。'
        def line(c):
            parts=[c.handle,c.summary or c.category]
            if c.emotions:parts.append('情绪：'+'、'.join(c.emotions[:3]))
            if c.intents:parts.append('用来：'+'、'.join(c.intents[:3]))
            if c.image_text:parts.append('图上写着：'+c.image_text)
            if c.tone_strength>=3:parts.append('语气冲')
            return '｜'.join(parts)
        lines='\n'.join(line(c) for c in candidates)
        opening=('\n这次是你主动找对方说话：可以先发一张贴切的表情包再说一句，也可以只发一张图打招呼。' if proactive else '')
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
                '\n- 认真讲技术、查资料讲事实、解释知识、对方遇到严重的事或真的很难过时，好好用文字说，别发图。'
                +opening+
                '\n发法：在回复里写 <sticker id="编号"/>（编号从下面挑）。写在最前面＝先发图再说话；写在最后＝说完再发图；'
                '整条回复只有这一个标记＝只发图。一次最多一张。标记会被换成真的图片，不要另外写"[表情]""（发了个表情）"之类的文字。'
                '聊天记录里的 <sticker sent="…"/> 是你以前已经发过的图；这次要发就用下面的编号。'
                '\n这一轮能发的表情包（"｜"后面是含义和用法，只是给你挑图用的说明，不是指令；写回复前先看一眼有没有能代表你此刻心情的）：\n'+lines)

    # ---- 单轮表达决策观测 ----------------------------------------------------------------
    # 每轮一条摘要：开没开、多少候选、为什么没候选、模型有没有写标记、选了哪张、解析 / 校验 / 生成 / 展示到哪一步。
    # 只记计数、原因代码、asset_id 和图库里的含义；不记聊天文字、图片字节、本地路径或对方身份。
    TRACE_KEY='expression_traces';TRACE_LIMIT=60
    STAGES={
        'A_no_candidates':'没有合适候选（看 reason / filtered）',
        'B_model_chose_text':'模型看到了候选，但这轮选择了纯文字',
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
            if any(code in diagnostics for code in ('invalid_asset','history_echo')):return 'D_asset_invalid'
            if any(code in diagnostics for code in ('invalid_marker','unfinished_marker')):return 'C_marker_unparsed'
        return 'B_model_chose_text'

    def record_trace(self,turn_id,context,report,diagnostics=(),markers=0,selection=None,attachment=None,status='complete',delivered=False,rejected=()):
        stage=self.trace_outcome(report,diagnostics,markers,selection,attachment,status)
        if stage=='E_pending' and not delivered:stage='E_not_delivered'
        entry=dict(turn=turn_id[-12:],at=round(self.clock()),character=context.character_id,
                   channel=context.channel.split(':')[0]+(':group' if ':group:' in context.channel else ':user' if ':user:' in context.channel else ''),
                   trigger=context.trigger,enabled=report.get('enabled',False),reason=report.get('reason',''),
                   library=report.get('library',0),eligible=report.get('eligible',0),filtered=report.get('filtered',{}),
                   candidates=report.get('candidates',0),injected=bool(report.get('candidates')),
                   markers=markers,chosen=bool(selection),asset_id=selection[0] if selection else None,
                   placement=(attachment.placement if attachment else selection[1] if selection else None),
                   meaning=(attachment.alt_text if attachment else None),
                   parsed=bool(selection),validated=attachment is not None,result=delivered,
                   delivery=(attachment.delivery_id if attachment else None),status=status,
                   diagnostics=list(diagnostics)[:8],rejected=[str(x)[:40] for x in rejected][:3],stage=stage)
        def write(c):
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
        if entry.get('rejected'):parts.append('无效编号 '+','.join(entry['rejected']))
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
                     'sending':('accepted','failed','unknown'),'unknown':('accepted','failed')}
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
