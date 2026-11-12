from __future__ import annotations

import time
from uuid import uuid4
from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.services.context_service import ContextService
from aicompanion.services.emotion_service import EmotionService


class ChatController:
    """纯业务流程可由桌面或后续安卓适配器调用，不依赖 Qt。"""

    def __init__(self,database,memory_service,models):
        """models：模型解析器（ModelRouter）。传入的是单个客户端时（测试、诊断脚本）包成 FixedRouter。"""
        self.database,self.memory_service = database,memory_service
        from aicompanion.providers.router import FixedRouter
        self.router = models if hasattr(models,'resolve') else FixedRouter(database,models)
        self.character = database.characters()[0] if database.characters() else DEFAULT_CHARACTER
        self.context = ContextService(database,memory_service)
        self.emotions = EmotionService(database)
        # 每个角色的表情 / 动作词表（来自角色包）；模型可在台词里标注，存库前去掉
        self.vocabs = {}
        # P5：允许在回复里用 <task> 提议动手（由界面根据设置打开）
        self.agent_enabled = False
        from aicompanion.expression.service import ExpressionService
        self.expression = ExpressionService(database)
        # T15：她对每个人的关系（同一套，桌面和 QQ 共用）；后台从真实聊天里理解关系事件
        from aicompanion.relationship.service import RelationshipService
        from aicompanion.relationship.analyzer import RelationshipAnalyzer
        self.relationships = RelationshipService(database)
        self.relationship_analyzer = RelationshipAnalyzer(database, self.relationships)

    # ---- 模型 ---------------------------------------------------------------------------------
    def client(self,character_id=None,purpose='companion'):
        """这个角色、这个用途此刻该用的聊天客户端（计划书 v4 功能一）。
        调用方拿到后自己保存，用它把这一轮说完；后台线程不要中途再来取。"""
        return self.router.client('chat',character_id or self.character.id,purpose)[0]

    def _fit(self,character_id,purpose,history,prompt,sections,marks):
        from aicompanion.services import budget
        window=max_output=resolved=None
        try:
            resolved=self.router.resolve('chat',character_id,purpose)
            window,max_output=getattr(resolved,'context_window',None),getattr(resolved,'max_output',None)
        except Exception: pass
        base=marks.get('base',len(prompt)); rel=marks.get('relationship',base); other=marks.get('other',rel); tools=marks.get('tools',other)
        detail=dict(sections,relationship=rel-base,other=other-rel,tools=tools-other,expression=len(prompt)-tools)
        kept,report=budget.fit(history,prompt,purpose,window,max_output,detail)
        budget.save(self.database,character_id,report)
        if report.blocked:   # 确定超过已知模型上限：不发（不删人格、不删当前这句话）
            raise budget.BudgetExceeded(budget.refusal(report,getattr(resolved,'label','')))
        return kept

    def use_client(self,client,**options):
        """测试与桌宠自检：所有能力都改用这个客户端（options 传给 FixedRouter，例如 vision_model）。"""
        from aicompanion.providers.router import FixedRouter
        self.router=FixedRouter(self.database,client,**options)

    def search_factory(self,character_id):
        """查梗 / 学梗用的搜索服务工厂：按这个角色的搜索设置，而不是聊天接口的主机名。"""
        target=self.router.search_target(character_id)
        from aicompanion.community.search import WebSearch
        return lambda _client=None:WebSearch(self.database,target=target)

    def record_user_message(self,content,source='text',channel='desktop'):
        message_id = self.database.add_message('user',content,self.character.id,source=source,channel=channel)
        self.emotions.update(self.character.id,content,'desktop' if source!='qq' else channel)
        if source!='qq':   # 桌面上说话 = 主人来了一次；QQ 上的由桥接按 QQ 身份记
            try:
                from aicompanion.relationship.service import OWNER_DESKTOP
                self.relationships.resolve(OWNER_DESKTOP,self.character.id)
            except Exception: pass
        self.memory_service.maybe_extract_memory(content,self.character.id,message_id)
        return message_id

    def generate(self,turn_id,character,cancellation,query='',proactive='',channel='desktop',scene='pet',situation='',source='text',offer_tasks=True,purpose='companion',through_id=None,reply_filter=None,*,expression_context=None,on_expression_event=None,person=None,settle_dates=True):
        """所有自然聊天共用这一轮生成；图片控制在 yield 给 UI/TTS 之前剥离。
        settle_dates=False：主动发到桌面要等界面显示，QQ 要等插件回执，这一轮占住的纪念日提醒先不确认，
        由调用方在送达后 confirm、没送达时 release；生成失败 / 取消照样当场释放。"""
        from dataclasses import replace
        from aicompanion.expression.domain import ExpressionContext,ExpressionEvent
        from aicompanion.expression.stream import ExpressionStream
        from aicompanion.pet.script import Vocab,strip_cues
        context=expression_context or ExpressionContext(turn_id,character.id,'desktop:'+character.id,
            channel,scene,'proactive' if proactive else 'user_reply',proactive,supports_images=bool(on_expression_event))
        if context.generation_id!=turn_id or context.character_id!=character.id:
            raise ValueError('表达上下文与当前轮次不一致')
        context=replace(context,supports_images=context.supports_images and on_expression_event is not None,
                        early_before=context.early_before and reply_filter is None)
        # 绑定主人的 QQ 私聊也复用 desktop 历史频道，必须同时检查实际来源。
        local_reply = (channel == 'desktop' and scene == 'pet' and source in ('text', 'voice') and not proactive
                       and context.scope == 'desktop:'+character.id and context.channel == 'desktop'
                       and context.scene == 'pet' and context.trigger == 'user_reply')
        self.database.start_turn(turn_id,character.id)
        parts=[];status='complete';error='';attachment=None;parser=None;candidates=();held=''
        report=dict(enabled=False,reason='not_prepared',library=0,eligible=0,filtered={},candidates=0)
        try:
            history,prompt,sections=self.context.build_with_sections(character,query,channel,scene,situation,through_id,turn_id=turn_id)
            marks={'base':len(prompt)}
            if local_reply:
                if self.database.get_setting('self_exit_enabled', True):
                    prompt += ('\n如果你此刻确实想离开桌面，可以先用自己的口吻道别，再在末尾写 '
                               '<leave reason="想一个人静一静"/>。reason 用一句能直接对用户说的简短原因，'
                               '不要写分析过程。普通聊天不用这个标记，求助、办事和涉及安全时先认真回应。')
                else:
                    prompt += '\n想暂时离开时，可以告诉用户你想安静一会儿。'
            if proactive:
                from aicompanion.services.companion_dates import DATE_EVE_INTENT, CompanionDates
                if proactive == DATE_EVE_INTENT:
                    reminder = (CompanionDates(self.database).eve_prompt(character.id, turn_id)
                                if channel == 'desktop' and scene == 'pet' else '')
                    if not reminder: raise ReplyCancelled('提前提醒已失效')
                    prompt += '\n' + reminder
                prompt+='\n现在是你主动开启话题：'+proactive+'。像真人发消息那样开个头：一句也行，想说的多就连着说几句（一句一行，每行单独发出去；句子中间停顿用逗号，别用空格），不编造经历。'
            # T15：她和对方的关系、此刻的心情精力、对方最近多常找她做事——给同一个大脑做判断的依据，不规定回应方式
            person_id=self._person(person,channel,scene,context)
            if person_id:
                from aicompanion.relationship.service import scope_of
                emotion=self.database.load_emotion(character.id)
                prompt+=self.relationships.prompt(character.id,person_id,scope_of(channel),mood=emotion.mood,energy=emotion.energy,public=scene!='pet')
            marks['relationship']=len(prompt)
            prompt+=self._community(character,query,channel)
            vocab=self.vocabs.get(character.id)
            if vocab:prompt+=vocab.prompt()
            marks['other']=len(prompt)
            if self.agent_enabled and not proactive and offer_tasks:
                from aicompanion.pet.script import TASK_PROMPT
                prompt+=TASK_PROMPT
            marks['tools']=len(prompt)
            candidates,report=self.expression.prepare_report(context,character,query,history)
            prompt+=self.expression.prompt(candidates,proactive=bool(proactive),qq=context.scope.startswith('qq:'))
            if context.more_memes and candidates:prompt+='\n对方说过喜欢你多发表情包；合适的时候可以更常用，但仍由你自己决定。'
            parser=ExpressionStream([c.asset_id for c in candidates],self.expression.legacy_map(candidates),
                                    {c.handle:c.asset_id for c in candidates if c.handle},
                                    {c.summary:c.asset_id for c in candidates if c.summary},
                                    set(vocab.expressions)|set(vocab.motions) if vocab else None)
            choices={c.asset_id:c for c in candidates}
            from aicompanion.usage.meter import using
            client_purpose='proactive' if proactive else ('qq' if channel.startswith('qq:') else purpose)
            client=self.client(character.id,client_purpose)
            # 计划书 v4 功能六：整份请求预算。人格、记忆、召回、关系、表情候选、办事说明都已拼好，超了从最早的历史裁
            history=self._fit(character.id,client_purpose,history,prompt,sections,marks)
            with using(purpose,character.id,turn_id):
                for token in client.generate_reply_stream(history,prompt,cancellation):
                    cancellation.check()
                    clean=parser.feed(token)
                    if context.early_before and attachment is None and parser.selection and parser.selection[1]=='before_text':
                        attachment=self.expression.attachment(turn_id,context,choices[parser.selection[0]],'before_text')
                        cancellation.check()
                        if attachment:on_expression_event(ExpressionEvent('attachment_ready',turn_id,attachment))
                        cancellation.check()
                    if clean and not ''.join(parts).strip():clean=clean.lstrip()   # 图在前时正文开头的空行去掉
                    # 末尾的空行先压着：后面还有字才放出去，图在后时正文结尾不留一串空行
                    clean=held+clean if clean else clean
                    body=clean.rstrip();held=clean[len(body):] if clean else held
                    if body:parts.append(body);yield body
            cancellation.check()
            tail=(held+parser.finish()).rstrip()
            if tail.strip():parts.append(tail);yield tail
            cancellation.check()
            if attachment is None and parser.selection:
                aid,placement=parser.selection
                attachment=self.expression.attachment(turn_id,context,choices[aid],placement)
            if not ''.join(parts).strip() and attachment is None:
                if local_reply and parser.leave_reason:
                    parts.append('我想先安静一会儿。')
                    yield parts[-1]
                else:
                    raise RuntimeError('模型未返回有效内容。')
        except ReplyCancelled:
            status='cancelled'
            if parser:parser.cancel()
            raise
        except GeneratorExit:
            status='cancelled';cancellation.cancel()
            if parser:parser.cancel()
            raise
        except Exception as exc:
            status,error='failed',str(exc)
            raise
        finally:
            import re as _re
            content=_re.sub(r'\n{3,}','\n\n',strip_cues(''.join(parts),self.vocabs.get(character.id) or Vocab())).rstrip()
            if reply_filter is not None:
                try:
                    filtered=reply_filter(content,status)
                    if content.strip() and not filtered.strip():status='suppressed' if status=='complete' else status
                    content=filtered
                except Exception:
                    status='failed';error='回复过滤失败'
            if settle_dates or status!='complete':self._settle_dates(character,turn_id,status)
            if attachment and self.expression.catalog.resolve(attachment.asset_id,attachment.revision) is None:
                self.expression.receipt(attachment.delivery_id,'cancelled');attachment=None
            result=self.expression.result(turn_id,context,content,attachment,status,parser.diagnostics if parser else ())
            self.database.finish_turn(turn_id,character.id,content,status,error,channel=channel,source=source)
            self.expression.record_trace(turn_id,context,report,parser.diagnostics if parser else (),
                                         parser.markers if parser else 0,parser.selection if parser else None,
                                         result.attachments[0] if result.attachments else None,status,
                                         delivered=on_expression_event is not None,rejected=parser.rejected if parser else ())
            if on_expression_event:
                reason = parser.leave_reason if local_reply and status == 'complete' and content and parser else ''
                on_expression_event(ExpressionEvent('final',turn_id,result=result,leave_reason=reason))
            self._after_turn(character,channel,scene,person,context,parser,status,turn_id,proactive=bool(proactive))

    def _settle_dates(self,character,turn_id,status):
        """计划书 v4 功能九：这一轮占住了“今天是特别的日子”的提醒——成功说完就记成今天提过（一天只提一次，重启也不重复）；
        失败、取消、回复被拦下就放掉，下一轮还能提。按轮次 id 认领，桌面和主人 QQ 私聊同时生成也互不覆盖（Codex PR #43 R4）。"""
        try:
            from aicompanion.services.companion_dates import CompanionDates
            dates=CompanionDates(self.database)
            if status=='complete':dates.confirm(character.id,turn_id)
            else:dates.release(character.id,turn_id)
        except Exception:
            pass

    def _person(self,person,channel,scene,context):
        """这一轮在和谁说话：桥接传进来的人物；没传、又是桌面 / 爹的私下相处，就是主人。"""
        if person: return person
        if channel=='desktop' and scene=='pet':
            try: return self.relationships.owner_person()
            except Exception: return None
        return None

    def _after_turn(self,character,channel,scene,person,context,parser,status,turn_id,proactive=False):
        """她这轮写了 <mind/>：请她做事就记一条 request（不改分，只统计"多常找她做事"）；
        写了 feel / toward（对方让她明显高兴或不爽）就即时记一条关系事件；够条数时后台理解关系事件，吵起来时提前。
        关系记录出错不影响聊天。
        proactive：她主动开口的一轮（分享网页、心事、问候……），这一轮对方什么都没说。<mind/> 里的判断只可能来自提示里的材料
        （比如网页里引用的第三方骂人的话），不能当成主人的言行记进关系档案、也不推动心情（Codex PR #48 第三轮）。"""
        try:
            person_id=None if proactive else self._person(person,channel,scene,context)
            mind=getattr(parser,'mind',None) if parser else None
            self.last_mind=(turn_id,dict(mind) if mind else {})   # T20：桥接看她这一轮有没有要查的词
            heated=False
            if person_id and mind and status=='complete':
                from aicompanion.relationship.service import scope_of
                if mind.get('intent') or mind.get('willing') is not None:
                    note=(mind.get('intent') or '')+(f" 意愿{mind['willing']:.1f}" if mind.get('willing') is not None else '')
                    self.relationships.record(character.id,person_id,'request',1.0,scope=scope_of(channel),evidence=note,key='mind:'+turn_id)
                kind,impact,evidence=self._felt(mind)
                if kind:
                    # 负责人 2026-11-09：吵架要掉好感、她要有脾气。她自己当场的判断即时记一笔（不等后台分析），
                    # 程度由她判断，换算仍走动态规则（两端弹性、首次缓冲、单件上限）。
                    # T18：先看这句冲谁、谁的话——转述别人的话不扣分，攻击别人记"攻击别人"，冲她来的才按好恶记
                    self.relationships.record(character.id,person_id,kind,0.8,scope=scope_of(channel),evidence=evidence,key='feel:'+turn_id,impact=impact)
                    heated=kind in ('quarrel','insult')
            if status=='complete':
                # 计划书 v4 功能三：同一轮的判断顺带推动心情（不额外调用模型）。她有明确判断时以判断为准，
                # 没有判断才用这条消息的用词兜底（Codex PR #38 第 3 项）
                pending=getattr(self.emotions,'pending_mood',None); self.emotions.pending_mood=None
                try:
                    from aicompanion.services.mood_service import MoodService
                    mood=MoodService(self.database)
                    judged=mood.on_mind(character.id,mind,channel) if mind and not proactive else None
                    if judged is None and pending and pending[0]==character.id and pending[1]==channel and time.time()-pending[3]<600:
                        mood.apply_fallback(character.id,pending[2],channel)
                except Exception: pass
            if status=='complete' and not proactive:
                # 吵起来了就早点让后台把这段对话理一遍（不用等攒够 6 条）
                self.relationship_analyzer.maybe_run(character.id,channel,self.client(character.id,'relationship'),
                                                     trigger=2 if heated else None,search=self.search_factory(character.id))
        except Exception:
            pass

    def _community(self,character,query,channel):
        """T18：这几句里她认识的梗 / 黑话（含义、语气、圈子、演变、来源），群聊再加这个群的共同记忆。出错不影响聊天。"""
        try:
            from aicompanion.community.lexicon import Lexicon
            from aicompanion.community.lore import GroupLore
            rows=self.database.recent_messages(4,character.id,channel=channel)
            text='\n'.join([str(r['content']) for r in rows]+[str(query or '')])
            group=channel.startswith('qq:group:')
            from aicompanion.community.learner import local_scope
            out=Lexicon(self.database).prompt(text,local_scope(channel))   # 本会话推断的用法 + 公开来源的通用义，不串别的群
            if group:out+=GroupLore(self.database).prompt(character.id,channel)
            return out
        except Exception:
            return ''

    @staticmethod
    def _felt(mind):
        """<mind at voice tone feel toward/> → (事件类型, 影响, 依据)。没有可记的返回 (None, None, '')。
        - voice=quote（引用 / 转述别人的话）：记 report，不改分；
        - at=other / all 且在攻击 / 反讽：记 third_party_attack（他在欺负别人，不是冲她）；
        - 冲她来的（at=me，或没写 at 但写了 toward）：认真攻击 insult、反讽 quarrel、其余按 toward 正负 quarrel / friendly。"""
        at,voice,tone,toward=mind.get('at') or '',mind.get('voice') or '',mind.get('tone') or '',mind.get('toward')
        label=' '.join(x for x in (mind.get('feel') or '',f'at={at}' if at else '',f'voice={voice}' if voice else '',f'tone={tone}' if tone else '',
                                   f'{toward:+.0f}' if toward else '') if x)
        if voice=='quote':return ('report',None,label)
        if at in ('other','all') and tone in ('attack','sarcasm'):return ('third_party_attack',None,label)
        if at in ('other','all','none'):return (None,None,'')
        if tone=='attack':return ('insult',{'affinity':toward if toward and toward<0 else -2.5,'trust':-1.0},label)
        if tone=='sarcasm' and not (toward and toward>0):return ('quarrel',{'affinity':toward if toward else -1.5,'trust':-0.5},label)
        if toward:return ('quarrel' if toward<0 else 'friendly',{'affinity':toward,'trust':toward*0.3 if toward<=-2 else 0.0},label)
        return (None,None,'')

    def stream_user_message(self,content):
        self.record_user_message(content)
        yield from self.generate(str(uuid4()),self.character,CancellationToken(),content)
