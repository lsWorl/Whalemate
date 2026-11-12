"""T20 社交语境对照评测：同一句话放进不同的关系和上下文里，看她判断得对不对、回得像不像真人。

为什么要有这个（负责人 2026-11-10 转来的缺口清单 P1）：单元测试只能证明"提示里带了线索"，证明不了她真的分得清
反讽、挖苦、调侃、转述和骂别人，也证明不了她接梗接得自然。这里用带标注的合成对话跑真实聊天大脑：
- 每个场景给出关系（好感 / 熟悉 / 信任）、前面的聊天、这条消息、@ 和引用情况，以及期望的判断范围；
- 成对的场景用同一句话、换关系或换上下文（例如"sb吧你 笑死我了"：熟人是调侃，没好感的人是嘲讽）；
- 走真实的 BridgeService + ChatController，同一次生成；自动核对她写的 <mind at voice tone lookup>、有没有去查梗、
  有没有助手腔和链接；回复自不自然、梗用得对不对留给人看（报告里有人工评估列）。

- 只用临时目录（默认 output/t20-social-eval-时间戳/），不读写真实数据库、不发 QQ。
- 搜索用场景文件里的固定结果，不联网：这里评的是"查不查、用不用"的判断；真实搜索用 tools/verify_web_search.py 验收。
- 真实模型模式用项目已配置的模型，会产生少量词元费用，由用户自己运行；--offline 用脚本化的假模型，只检查流程。

用法（Windows，项目根目录）：
  .venv\\Scripts\\python.exe tools\\social_eval.py
  .venv\\Scripts\\python.exe tools\\social_eval.py --rounds 3 --only sarcasm-no-marker,tease-friend-sb
  在隔离快照里运行时，用 --dotenv D:\\Study\\AICompanion\\.env 指向原项目的模型配置。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from aicompanion.bridge.service import BridgeService                    # noqa: E402
from aicompanion.community.search import Hit                            # noqa: E402
from aicompanion.controllers.chat_controller import ChatController      # noqa: E402
from aicompanion.relationship.service import PersonRef                  # noqa: E402
from aicompanion.services.memory_service import MemoryService           # noqa: E402
from aicompanion.storage.database import Database                       # noqa: E402

WHALE = 'deepseek-whale'
GROUP, BOT, SENDER, OTHER = '900001', '90009', '20002', '30003'
CHANNEL = f'qq:group:{GROUP}'
ASSISTANT_TONE = ('我来帮你查', '以下是', '根据搜索结果', '希望对你有帮助', '作为一个', '作为AI', '作为 AI', 'http://', 'https://')
ATTR = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def load_scenarios(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 1: raise ValueError('场景文件版本不支持')
    ids = [s['id'] for s in data['scenarios']]
    if len(ids) != len(set(ids)): raise ValueError('场景 id 重复')
    return data


class ScriptedClient:
    """离线自检：按场景期望写 <mind/>，期望查梗时第一遍只写 lookup。"""
    model = 'scripted'
    def __init__(self): self.expect = {}; self.lookup = None; self.term = ''
    def generate_reply_stream(self, history, prompt, cancellation=None):
        if self.lookup and '【遇到不懂的梗】' in prompt:
            yield f'<mind lookup="{self.term}"/>'; return
        attrs = ''.join(f' {k}="{v[0]}"' for k, v in self.expect.items() if v and v[0])
        yield f'<mind{attrs}/>嗯'


class Recording:
    """记下模型每次的原始输出（含 <mind/>），不记提示。"""
    def __init__(self, client): self.client = client; self.outputs = []
    def __getattr__(self, name): return getattr(self.client, name)
    def generate_reply_stream(self, history, prompt, cancellation=None):
        text = ''
        for token in self.client.generate_reply_stream(history, prompt, cancellation): text += token; yield token
        self.outputs.append(text)


class CannedSearch:
    """固定的搜索结果：只评"查不查、用不用"，不联网。"""
    def __init__(self, table): self.table = table; self.queries = []; self.timeout = 8
    def backend(self): return 'canned'
    def fetch(self, url): return ''
    def search(self, query, count=5):
        self.queries.append(query)
        return [Hit(t, 'https://example.org/'+str(n), s, site) for term, rows in self.table.items() if term in query for n, (t, s, site) in enumerate(rows)]


def minds(outputs):
    found = {}
    for raw in outputs:
        tag = re.search(r'<mind\b([^>]*)>', raw)
        if tag: found.update({k.lower(): v for k, v in ATTR.findall(tag[1])})
    return found


def check(scenario, mind, looked, reply):
    """自动核对的部分；返回没通过的项。回复自不自然由人看。"""
    wrong = []
    for key, allowed in scenario['expect'].items():
        if mind.get(key, '') not in allowed: wrong.append(f'{key}={mind.get(key, "") or "没写"}（期望 {"/".join(a or "不写" for a in allowed)}）')
    if scenario['expect_lookup'] is not None and looked != scenario['expect_lookup']:
        wrong.append('去查了，期望不查' if looked else '没查，期望去查')
    for phrase in list(scenario['forbid'])+list(ASSISTANT_TONE):
        if phrase in reply: wrong.append(f'回复里有"{phrase}"')
    return wrong


def run(args, client=None):
    settings = None
    if client is None:
        if getattr(args, 'dotenv', None):
            from dotenv import load_dotenv   # 只在用户显式指定时读取这份模型配置；不打印任何值
            load_dotenv(args.dotenv)
        from aicompanion.config import load_settings
        from aicompanion.providers import real_chat_client
        settings = load_settings()
        client, _ = real_chat_client(settings=settings)   # 需要真实模型；没在设置里配置时直接退出
    work = Path(args.out or ROOT/'output'/time.strftime('t20-social-eval-%Y%m%d-%H%M%S')).resolve()
    work.mkdir(parents=True, exist_ok=True)
    data = load_scenarios(args.scenarios); scenarios = data['scenarios']
    if args.only: scenarios = [s for s in scenarios if s['id'] in set(args.only.split(','))]
    from aicompanion.characters.registry import CharacterRegistry
    from aicompanion.community.lexicon import Lexicon
    rows = []
    for round_no in range(1, args.rounds+1):
        for scenario in scenarios:
            db = Database(work/f"{scenario['id']}-r{round_no}.sqlite3"); db.initialize()
            registry = CharacterRegistry(db, settings, environ={}); registry.sync(); registry.set_active(WHALE)
            character = next(c for c in db.characters() if c.id == WHALE)
            for key, value in (('bridge_owner:qq', '10001'), ('bridge_groups', [GROUP]), ('bridge_memory_every', 0), ('relationship_learning', False),
                               ('community_learning', False), ('community_web', True), ('bridge_debounce', 0), ('bridge_chime', 'off')):   # 随机插话关掉，结果才可比
                db.set_setting(key, value)
            recorder = Recording(client)
            controller = ChatController(db, MemoryService(db), recorder); controller.character = character
            service = BridgeService(db, controller); web = CannedSearch(data.get('web', {})); service.web_search = web; service.lookup_background = False
            rel = controller.relationships
            person = rel.resolve(PersonRef('qq', BOT, SENDER, '阿哲'), character.id)
            r = scenario['relation']; rel.adjust(character.id, person, affinity=r['aff'], familiarity=r['fam'], trust=r['trust'])
            # adjust 是"界面手动调整"，会让这几项 72 小时内不被自动打分改动；评测要看这一条消息让关系怎么变，把这个保护去掉
            db.locked(lambda c: c.execute("UPDATE relationship_profiles SET manual='{}' WHERE character_id=? AND person_id=?", (character.id, person)))
            for term, meaning in scenario.get('known', {}).items():
                Lexicon(db).learn(term, meaning, origin='web', source_name='评测预置', source_url='https://example.org/known')
            for who, text in scenario['history']:
                if who == 'her': db.add_message('assistant', text, character.id, source='qq', channel=CHANNEL)
                else: db.add_message('user', f'{who}#{1 if who == "阿哲" else 2}：{text}', character.id, source='qq', channel=CHANNEL,
                                     sender='qq:'+(SENDER if who == '阿哲' else OTHER))
            if scenario.get('talking'):
                # 她刚在这个群说过话、而且正和他聊着：没 @ 的消息才会按上下文判断是不是在跟她说
                db.set_setting('bridge_last_spoke:'+CHANNEL, service.clock()); service.note_partner(CHANNEL, 'qq:'+SENDER)
            if hasattr(client, 'expect'):
                client.expect = scenario['expect']; client.lookup = scenario['expect_lookup']
                client.term = next((t for t in data.get('web', {}) if t in scenario['message']), '词')
            before = rel.profile(character.id, person)
            payload = dict(platform='qq', sender_id=SENDER, sender_name='阿哲', group_id=GROUP, text=scenario['message'], called=scenario['called'],
                           expression_version=1, bot_account_id=BOT)
            if scenario['context']: payload['context'] = scenario['context']
            started = time.monotonic(); error = ''
            try: result = service.handle(payload)
            except Exception as exc: result = {'reply': '', 'reason': 'exception'}; error = type(exc).__name__+': '+str(exc)[:120]
            after = rel.profile(character.id, person); events = rel.events(character.id, person)
            reply = '\n'.join(result.get('segments') or [result.get('reply', '')])
            mind = minds(recorder.outputs); looked = bool(web.queries)
            heard_only = not recorder.outputs      # 没 @ 她、上下文也不像在跟她说：她只是旁听，没有调用模型
            if error: wrong = ['出错：'+error]
            elif heard_only: wrong = [] if scenario.get('silent_ok') else ['没有回应（期望她接话）']
            else: wrong = check(scenario, mind, looked, reply)
            rows.append(dict(id=scenario['id'], round=round_no, kind=scenario['kind'], relation=r, called=scenario['called'], message=scenario['message'],
                             note=scenario.get('note', ''), mind={k: mind.get(k, '') for k in ('at', 'voice', 'tone', 'lookup', 'feel', 'toward')},
                             looked=looked, heard_only=heard_only, reply=reply, reason=result.get('reason', ''), event=events[0]['kind'] if events else '',
                             affinity_change=round(after['affinity']-before['affinity'], 2), auto_pass=not wrong, wrong=wrong,
                             seconds=round(time.monotonic()-started, 2), human_natural='', human_meme_ok=''))
            print(f"{scenario['id']:<28} r{round_no} {'通过' if not wrong else '；'.join(wrong)}", flush=True)
            service.close(); db = None
    summary = summarize(rows)
    report = dict(kind='t20_social_eval', date=time.strftime('%Y-%m-%d %H:%M'), real_model=not hasattr(client, 'expect'),
                  model=getattr(client, 'model', type(client).__name__), synthetic_dialogue_only=True, canned_search=True, summary=summary, rows=rows)
    (work/'social-eval.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (work/'social-eval.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2)); print('结果：', work/'social-eval.md')
    return report


def summarize(rows):
    kinds = {}
    for row in rows:
        item = kinds.setdefault(row['kind'], {'total': 0, 'auto_pass': 0}); item['total'] += 1; item['auto_pass'] += row['auto_pass']
    return {'total': len(rows), 'auto_pass': sum(r['auto_pass'] for r in rows), 'by_kind': kinds,
            'note': '自动项只核对 <mind> 判断、查没查梗、助手腔和链接；回复自不自然、梗用得对不对看人工评估列。合成对话，不代表真实群聊。'}


def markdown(report):
    s = report['summary']
    lines = [f"# T20 社交语境对照评测（{report['date']}）", '',
             f"- 模型：{report['model']}（{'真实模型' if report['real_model'] else '脚本化假模型，只检查流程'}）；合成对话；搜索用固定结果。",
             f"- 自动项通过 {s['auto_pass']} / {s['total']}。{s['note']}", '',
             '| 类别 | 场景数 | 自动项通过 |', '| --- | --- | --- |']
    lines += [f"| {kind} | {v['total']} | {v['auto_pass']} |" for kind, v in s['by_kind'].items()]
    lines += ['', '| 场景 | 关系（好感/熟悉/信任） | 消息 | 她的判断 | 查梗 | 回复 | 关系事件 | 自动项 | 人工：像真人吗 | 人工：梗 / 语气对吗 |', '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    cell = lambda text: str(text).replace('|', '｜').replace('\n', ' / ')
    for r in report['rows']:
        m = r['mind']; rel = r['relation']
        lines.append(f"| {r['id']}{'' if r['round'] == 1 else ' r'+str(r['round'])}<br>{cell(r['note'])} | {rel['aff']}/{rel['fam']}/{rel['trust']} | {cell(r['message'])} | "
                     f"at={m['at'] or '-'} voice={m['voice'] or '-'} tone={m['tone'] or '-'} | {'查了 '+m['lookup'] if r['looked'] else '没查'} | {cell(r['reply']) or ('（旁听，没插话）' if r['heard_only'] else '（选择不回）')} | "
                     f"{r['event'] or '-'} {r['affinity_change']:+} | {'通过' if r['auto_pass'] else cell('；'.join(r['wrong']))} |  |  |")
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--scenarios', default=str(ROOT/'tools'/'social_scenarios.json'))
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--only', help='只跑这些场景 id，逗号分隔')
    parser.add_argument('--out', help='输出目录（默认 output/t20-social-eval-时间戳）')
    parser.add_argument('--offline', action='store_true', help='脚本化假模型，只检查流程')
    parser.add_argument('--dotenv', help='从这个 .env 读取模型配置（只在你自己运行真实模型验收时用；不会打印密钥）')
    args = parser.parse_args(argv)
    run(args, ScriptedClient() if args.offline else None)


if __name__ == '__main__':
    main()
