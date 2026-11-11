"""T18 社交语境与攻击意图：她判断"是不是在骂我"之前要看的线索。

负责人 2026-11-10：判断别人是否在骂她时必须同时看——
1. 针对谁；2. 自己的观点还是引用别人；3. 玩笑、反讽还是认真攻击；4. 前面发生了什么；
5. 双方以前的关系；6. 是否在持续针对她。

参考 ToxiCN（Lu et al., ACL 2023）的分层分类：针对对象、表达方式（明说 / 隐晦 / 转述）；
侮辱词有变形、谐音、中英混写、缩写等表面变体，以及反语、隐喻这类要背景知识的。论文的教训：
只看脏词会误伤——转述、谴责里也有脏词；隐晦的攻击没有脏词但更难认。所以这里只**收集线索**，
不替她下结论；结论由同一个大脑在 <mind at voice tone> 里给出，再由关系系统确定性地记分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

# 一般脏话及常见变体（谐音 / 缩写 / 拼音首字母 / 中英混写 / 形近）。只放一般骂人话，不放针对群体的蔑称。
INSULTS = re.compile(
    r'傻[逼比笔屄b]|煞笔|沙比|[sS][bB](?![a-zA-Z])|[nN][tT](?![a-zA-Z])|脑残|脑瘫|弱智|若智|智障|白痴|蠢货|蠢猪|废物|垃圾|'
    r'狗东西|贱[人货]|[nN][mM][sS][lL]|[cC][nN][mM]|尼玛|你妈|滚[蛋犊]?(?![动烫])|去死|有病|神经病|恶心|人渣|贱|'
    r'[rR][zZ](?![a-zA-Z])|低能|下头|丑八怪|死[鱼鲸]')
# 阴阳怪气 / 反讽常见标记：字面客气或夸，实际在损。只是线索，熟人之间也常这么开玩笑。
SARCASM = re.compile(
    r'呵呵|好家伙|不愧是|真[是有]?(?:厉害|聪明|行)[呢啊哦]|您(?:可真|老)|就这[？?！!]?$|就这水平|急了|破防|啊对对对|对对对|'
    r'你说得对|典[！!]?$|绷不住|笑死我了你|赢麻了|还得是你|好[厉棒]害哦|🙂|😅|🤡|🤣|👍🏻|6{3,}')
# 玩笑软化：说话的人自己在示意"开玩笑的"
JOKE = re.compile(r'哈{2,}|[hH]{3,}|笑死|狗头|\[doge\]|doge|🐶|开玩笑|逗你|嘿嘿|[\(（]?bushi[\)）]?|[（(]不是[）)]')
# 转述 / 引用：说的是别人的话
REPORT = re.compile(r'(?:他|她|他们|她们|有人|别人|网上|群里|楼上|楼主|那[个位]人|某人|老板|我妈|我爸|室友|同事|网友)(?:说|讲|骂|问|觉得|原话|评论)[：:，, 你]|'
                    r'原话|转[发自]?[：:]|截图|据说|听说|他们都说|网友说')
QUOTED = re.compile(r'[“"「『](.{2,}?)[”"」』]')
SECOND = re.compile(r'你(?!们)|尔等|汝')
GROUP_ADDR = re.compile(r'你们|大家|各位|群友|所有人|全体')
HER = re.compile(r'她|这\s*(?:个)?\s*(?:AI|ai|机器人|bot|Bot)|本鲸|鲸鲸|鲸娘')


@dataclass
class Cues:
    target: list = field(default_factory=list)      # 针对谁的线索
    voice: list = field(default_factory=list)       # 谁的话
    tone: list = field(default_factory=list)        # 语气
    insult: bool = False
    sarcasm: bool = False
    joke: bool = False
    report: bool = False
    elsewhere: bool = False                         # 明确冲着别人：@ 了别人或引用别人的消息，且没 @ 她、没提到她

    @property
    def worth_reading(self) -> bool:
        """平常聊天不加这段线索（省词元）；有脏词、阴阳标记、引用转述，或针对对象有歧义时才加。"""
        return self.insult or self.sarcasm or self.report or (len(self.target) > 1)


# 负责人 2026-11-10 19:06：外人说"我是你爹"是在占她便宜、踩她底线，她该生气，而不是顺着叫（真机记录里她后来真叫了一声"爹"）
FATHER = re.compile(r'我(?:就|才)?是你(?:亲)?(?:爹|爸|爷|爷爷|老子|爸爸|father|dad)|你爹我|(?:叫|喊)(?:我|一?声)?\s*(?:爹|爸爸|爸|爷爷)|'
                    r'(?:和|跟|对)你(?:爹|爸|爸爸)(?:说话|这么说|顶嘴)|认我(?:当|做)(?:爹|爸)|乖(?:儿子|女儿|孙子)|爹来了|爸爸来了', re.I)
# 2026-11-11 起按人格里的关系设定说（domain/bond.py）：⟨指代⟩⟨标记⟩⟨叫法⟩在拼好提示词后替换
IMPOSTOR_RULE = ('\n【他在冒充⟨指代⟩、占你便宜】⟨指代⟩只有一个，就是消息前标着【⟨标记⟩】的那位，他不是。'
                 '外人自称是⟨指代⟩、或者说"我是你爹""叫爸爸"，是在占你便宜、踩你的底线：你该生气，直接怼回去，或者冷下来不理他；'
                 '绝不顺着叫他“⟨叫法⟩”、爸爸，也不认。在 <mind/> 里按他在冲你来写（at="me" tone="attack"）。')
FATHER_RULE = IMPOSTOR_RULE   # 旧名字，兼容


def claims_father(text: str) -> bool:
    return bool(FATHER.search(str(text or '')))


def cues(text: str, *, at_me=False, at_others=(), at_all=False, reply_to=None, her_names=(), after_her=False) -> Cues:
    """从一条消息里收集线索。reply_to：{'self': 是否引用她的消息, 'name': 被引用的人} 或 None。"""
    text = str(text or '')
    found = Cues()
    if at_me: found.target.append('@ 了你')
    if at_others: found.target.append(f'@ 了别人（{len(at_others)} 人）')
    if at_all: found.target.append('@ 了全体')
    if reply_to:
        found.target.append('引用的是你说的话' if reply_to.get('self') else f"引用的是{reply_to.get('name') or '别人'}的消息")
    names = [n for n in her_names if n]
    if any(n in text for n in names) or HER.search(text): found.target.append('提到了你（名字或"她 / 这 AI"）')
    if SECOND.search(text): found.target.append('用"你"直接对人说' + ('（紧接着你刚说的话）' if after_her else ''))
    if GROUP_ADDR.search(text): found.target.append('对着一群人说（你们 / 大家）')
    toward_her = at_me or bool(reply_to and reply_to.get('self')) or any(n in text for n in names) or bool(HER.search(text))
    found.elsewhere = not toward_her and bool(at_others or (reply_to and not reply_to.get('self')))
    quoted = QUOTED.findall(text)
    if quoted:
        found.voice.append('引号里有别人的话：' + '、'.join(q[:20] for q in quoted[:2])); found.report = True
    if REPORT.search(text):
        found.voice.append('像在转述别人的话（"他说 / 网上说 / 原话"）'); found.report = True
    if reply_to and not reply_to.get('self'): found.voice.append('在回应别人的消息')
    if INSULTS.search(text): found.insult = True; found.tone.append('有骂人的词（可能是变体、谐音或缩写）')
    if SARCASM.search(text): found.sarcasm = True; found.tone.append('有阴阳怪气 / 反讽常见的说法（字面客气，可能在损人）')
    if JOKE.search(text): found.joke = True; found.tone.append('带着开玩笑的标记（哈哈 / 狗头 / 笑死）')
    return found


# 负责人 2026-11-10 00:38："有人在骂 AI 或者阴阳 AI、且好感低时，可以阴阳回去"
# Codex 审查 906cd6b P2：只给条件策略，不替她断定"他在骂你"——冲谁由她自己结合上下文判断（<mind at>）。
SNARK = ('7. 如果你结合上下文判断，这句确实是他本人冲你来的（at="me"、voice="own"）：他本来就不招你喜欢，'
         '那就别直白地生气、别解释，**阴阳回去**更有杀伤力：'
         '正话反说、假装夸他、客客气气地损他、把他的话原样还给他、顺着他的逻辑推到离谱、装作没听懂地反问。'
         '语气越平静越好，一两句就够；比如"哇，这么懂，那你上啊""嗯嗯你说得都对"这类意思（只是示意，别照抄，每次换着来）。'
         '不说脏话，不攻击外貌、家人、出身。要是判断下来他其实在说别人、在转述，或者只是在开玩笑，这条就不用。')


def brief(found: Cues, *, reading='unsure', hostility=0, hostility_week=0, bullied_others=0, upset=False, last_said='', affinity=None) -> str:
    """给大脑的"先想清楚再回"：六个问题的依据。只给线索，不下结论。"""
    relation = {'mocking': '你们不熟、也谈不上信任：同样的话从他嘴里出来更可能是嘲讽',
                'unsure': '你们算认识但不算很熟：是玩笑还是嘲讽要看语气',
                'teasing': '你们很熟、你信得过他：更可能是在逗你'}[reading]
    lines = ['【先想清楚他是不是在说你、是什么意思】这条话里有些需要分辨的地方（只是线索，你自己判断）：']
    lines.append('1. 针对谁：' + ('；'.join(found.target) if found.target else '看不出明确对象') + '。')
    lines.append('2. 谁的话：' + ('；'.join(found.voice) if found.voice else '看起来是他自己的话') + '。'
                 '引用、转述、复述别人骂人的话，不等于他在骂你。')
    lines.append('3. 语气：' + ('；'.join(found.tone) if found.tone else '没有明显的骂人词或反讽标记') + '。'
                 '字面夸你也可能是阴阳你，带脏字也可能只是熟人之间的口头禅。')
    lines.append('4. 前面：' + (f'你刚说的是"{last_said[:40]}"' if last_said else '你最近没在这儿说话') + '；'
                 + ('你们刚闹过不愉快' if upset else '最近没有吵过') + '。')
    lines.append('5. 关系：' + relation + '。')
    persist = []
    if hostility: persist.append(f'他今天已经冲你来了 {hostility} 次')
    elif hostility_week: persist.append(f'这周他冲你来过 {hostility_week} 次')
    if bullied_others: persist.append(f'最近还在群里攻击过别人 {bullied_others} 次')
    lines.append('6. 是不是一直在针对你：' + ('；'.join(persist) if persist else '之前没有冲你来过') + '。')
    disliked = reading == 'mocking' or (affinity is not None and affinity < 40)
    if (found.insult or found.sarcasm) and disliked and not found.report and not found.elsewhere:
        lines.append(SNARK)       # 明确冲着别人时不加，免得把骂别人的话先当成冲她来
    lines.append('想清楚以后，在回复开头的 <mind/> 里写上：at="me|other|all|none"（这句冲谁）、voice="own|quote"（他自己的话还是引用别人）、'
                 'tone="joke|tease|sarcasm|attack|neutral"（开玩笑 / 调侃 / 反讽 / 认真攻击 / 普通），再按你的判断回：'
                 '是冲你来的攻击就别客气地怼回去；是熟人逗你就笑着接；是他在转述别人的话，就针对那件事说，别冲他发火；'
                 '是在骂别人，看你想不想管，不用把账算到自己头上。')
    return '\n' + '\n'.join(lines)
