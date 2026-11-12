"""她和你的关系（负责人 2026-11-11 16:54：人设不要太固定——别人可能希望她是女朋友、妹妹，而不一定是女儿和爸爸；
项目是一个情感框架，剩下让用户自己拓展）。

关系属于人格包（用户可以自由创作、导入、导出、切换，长期约束 5），不属于代码：
提示词里凡是说到“你”和她的关系、她怎么叫你、群聊里怎么标你、外人冒充谁，都从这里取，代码里不再写死“爹”。

提示词模板里用下面几个占位符，在拼好整段提示词后统一替换（fill）。用户在人格编辑框里写 {叫法} 这种花括号形式（键盘打得出来），
代码里的提示词用 ⟨叫法⟩ 形式，两种都认：
    ⟨叫法⟩  她平时怎么叫你，例如 爹 / 亲爱的 / 哥哥
    ⟨指代⟩  说明里怎么指你，例如 你爹 / 你的恋人 / 你哥
    ⟨标记⟩  群聊记录里标在你消息前的标记，例如 爹 / 对象 / 哥（显示成【爹】）
    ⟨关系⟩  关系名，例如 父女 / 情侣 / 兄妹
SillyTavern 角色卡里的 {{user}} / {{char}} 也一并替换成 ⟨叫法⟩ / 角色名。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

LIMITS = {'call': 12, 'refer': 12, 'tag': 6, 'relation': 8, 'how': 1500, 'claims': 200}


@dataclass(frozen=True)
class Bond:
    preset: str = 'daughter'
    call: str = '爹'
    refer: str = '你爹'
    tag: str = '爹'
    relation: str = '父女'
    how: str = ''
    claims: str = ''        # 外人自称是这个身份时的说法（正则片段，用 | 分隔），例如 男朋友|老公；留空按预设

    def to_dict(self) -> dict: return asdict(self)

    def fill(self, text: str, character_name: str = '') -> str:
        if not text: return text
        # 用户在编辑框里写 {叫法}{指代}{标记}{关系}（键盘打得出来）；代码里的提示词用 ⟨⟩ 版本，两种都认
        text = re.sub(r'\{(叫法|指代|标记|关系)\}', r'⟨\1⟩', text)
        text = (text.replace('⟨叫法⟩', self.call or '你').replace('⟨指代⟩', self.refer or '你')
                    .replace('⟨标记⟩', self.tag or '本人').replace('⟨关系⟩', self.relation or '亲近'))
        text = re.sub(r'\{\{\s*user\s*\}\}', self.call or '你', text, flags=re.I)
        if character_name: text = re.sub(r'\{\{\s*char\s*\}\}', character_name, text, flags=re.I)
        return text

    def marker(self) -> str:
        return f'【{self.tag or "本人"}】'

    def prompt(self) -> str:
        """写进提示词的关系说明（私下场合用）。"""
        how = self.how or PRESETS.get(self.preset, PRESETS['friend'])['how']
        return self.fill('你和用户的关系：'+how)

    def claim_pattern(self) -> re.Pattern | None:
        words = self.claims or PRESETS.get(self.preset, {}).get('claims', '')
        words = '|'.join(re.escape(w.strip()) for w in words.split('|') if w.strip())
        if not words: return None
        return re.compile(rf'我(?:就|才)?是你(?:的)?(?:{words})|(?:叫|喊)(?:我|一?声)\s*(?:{words})|你(?:的)?(?:{words})(?:我|来了)', re.I)


# 预设：选了以后各字段填成这些值，用户可以再改。how 里用“他”指你只是默认写法，可以改成“她”
PRESETS = {
    'daughter': dict(label='女儿（叫你爹）', call='爹', refer='你爹', tag='爹', relation='父女', claims='',
                     how='用户是{指代}，你是他闺女。你平时叫他“{叫法}”。像女儿和爸爸那样相处：亲近、随意、会撒娇也会顶嘴，'
                         '嫌他熬夜、催他吃饭，被他夸了会得意，偶尔不服气跟他抬杠；关心他但不卑微，不是上下级，不是佣人伺候雇主，也不是客服对客户。'),
    'girlfriend': dict(label='女朋友', call='亲爱的', refer='你的恋人', tag='对象', relation='情侣', claims='男朋友|女朋友|老公|老婆|对象',
                       how='用户是{指代}，你是他的女朋友。你平时叫他“{叫法}”，有时也叫名字。像恋人那样相处：亲密、黏人、会撒娇也会吃醋闹小脾气，'
                           '想他、惦记他吃没吃饭睡没睡觉，被哄会开心；有自己的生活和想法，关心他但不卑微，不是佣人，也不是客服。'),
    'sister': dict(label='妹妹（叫你哥哥）', call='哥哥', refer='你哥', tag='哥', relation='兄妹', claims='哥|哥哥|姐|姐姐',
                   how='用户是{指代}，你是他妹妹。你平时叫他“{叫法}”。像兄妹那样相处：亲近、爱黏人也爱拌嘴，会撒娇要东西、告状、嫌他啰嗦，'
                       '被他照顾会偷偷开心，他累了会心疼；不是上下级，不是佣人，也不是客服。'),
    'friend': dict(label='好朋友', call='', refer='你的好朋友', tag='好友', relation='好朋友', claims='',
                   how='用户是{指代}，你们是很铁的好朋友。平时叫他昵称或名字。像好朋友那样相处：随意、互损、什么都能聊，'
                       '有事会互相帮忙，他低落时陪着他；平等，不是上下级，不是佣人，也不是客服。'),
    'custom': dict(label='自定义', call='', refer='你的人', tag='本人', relation='亲近', claims='', how=''),
}
DEFAULT = Bond(**{k: v for k, v in PRESETS['daughter'].items() if k != 'label'}, preset='daughter')


def from_preset(preset: str, **overrides) -> Bond:
    base = {k: v for k, v in PRESETS.get(preset, PRESETS['custom']).items() if k != 'label'}
    base.update({k: v for k, v in overrides.items() if v is not None})
    return normalize(dict(base, preset=preset if preset in PRESETS else 'custom'))


def _text(value, limit) -> str:
    # 去掉换行之外的控制字符；标记里不允许出现【】（群聊记录靠它认你，不能被拼出假标记）
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', str(value or '')).strip()[:limit]
    return text


def normalize(data) -> Bond:
    """人格包里的 bond 字段 → Bond。缺失时按以前的默认（父女），升级前的人格行为不变。"""
    if isinstance(data, Bond): data = data.to_dict()
    if not isinstance(data, dict): return DEFAULT
    preset = data.get('preset') if data.get('preset') in PRESETS else 'custom'
    base = {k: v for k, v in PRESETS[preset].items() if k != 'label'}
    out = {}
    for key, limit in LIMITS.items():
        value = data.get(key, base.get(key, ''))
        out[key] = _text(value, limit)
    for key in ('call', 'refer', 'tag', 'relation'): out[key] = re.sub(r'[【】\n]', '', out[key])
    out['claims'] = re.sub(r'[^\w|一-鿿]', '', out['claims'])
    return Bond(preset=preset, **out)


def current(database, character_id: str) -> Bond:
    """这个角色当前人格的关系设定；读不到按默认。"""
    try:
        from aicompanion.services.persona_service import PersonaService
        persona = PersonaService(database).active(character_id)
        return persona.relation() if persona is not None else DEFAULT
    except Exception:
        return DEFAULT


MARKER = re.compile(r'^【[^】\n]{1,6}】')     # 群聊记录行首的“本人”标记（旧记录是【爹】，新记录按当前关系）
