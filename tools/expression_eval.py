"""T14 自然表达验收：用合成对话跑真实聊天大脑，记录每轮发没发表情、发了哪张，生成人工评估表。

- 走原 ChatController 同一次生成，没有额外选图请求；每个场景从同一个"已导入图库"的临时库复制一份，互不串话。
- 只用临时目录（默认 output/t14-eval-时间戳/），不读写用户真实数据库、不碰 .env 以外的账号配置、不发 QQ。
- 真实模型模式用项目已配置的模型（load_settings，与 verify_persona.py 相同），会产生少量词元费用，由用户自己运行；
  --offline 用脚本化的假模型，只检查流程，给单元测试用。

用法（Windows，项目根目录）：
  .venv\\Scripts\\python.exe tools\\expression_eval.py --pack D:\\Study\\AICompanion\\output\\t14-sticker-pack\\manifest.json
  .venv\\Scripts\\python.exe tools\\expression_eval.py --rounds 2       （每个场景跑两遍，看稳定性）
  不加 --pack 时只用仓库自带的 6 张原创姿态图。
  在隔离快照里运行时，用 --dotenv D:\\Study\\AICompanion\\.env 指向原项目的模型配置。
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from aicompanion.controllers.chat_controller import ChatController   # noqa: E402
from aicompanion.domain.cancellation import CancellationToken          # noqa: E402
from aicompanion.expression.domain import ExpressionContext, RelationshipSnapshot, owner_relationship   # noqa: E402
from aicompanion.services.memory_service import MemoryService           # noqa: E402
from aicompanion.storage.database import Database                       # noqa: E402

WHALE = 'deepseek-whale'


class ScriptedClient:
    """离线自检：有候选就按场景期望选第一张，text 场景只回文字。"""
    def __init__(self): self.calls = 0; self.expect = 'expressive'
    def generate_reply_stream(self, history, prompt, cancellation):
        import re
        self.calls += 1
        handles = re.findall(r'^(s\d+)｜', prompt, re.M)
        yield (f'<sticker id="{handles[0]}"/>好耶' if handles and self.expect != 'text' else '好的，我认真说一下。')


class Recording:
    """包一层，记下这一轮模型看到的候选行（含义和用法），方便判断"没发图"是没合适的图还是模型没选。不记提示里的其他内容。"""
    def __init__(self, client): self.client = client; self.candidates = []
    def __getattr__(self, name): return getattr(self.client, name)
    def generate_reply_stream(self, history, prompt, cancellation):
        import re
        self.candidates = re.findall(r'^s\d+｜[^\n]*', prompt, re.M)
        yield from self.client.generate_reply_stream(history, prompt, cancellation)


def load_scenarios(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 1: raise ValueError('场景文件版本不支持')
    return data['scenarios']


def prepare_library(work: Path, pack: str | None, settings=None):
    """建一个只有图库的模板库：自带 6 张原创图，或导入素材清单到 work/library（非系统盘目录由调用方保证）。"""
    template = work/'template.sqlite3'
    db = Database(template); db.initialize()
    from aicompanion.characters.registry import CharacterRegistry
    registry = CharacterRegistry(db, settings, environ={}); registry.sync(); registry.set_active(WHALE)
    from aicompanion.expression.catalog import AssetCatalog
    catalog = AssetCatalog(db)
    if pack:
        library = work/'library'; (library/'memes').mkdir(parents=True, exist_ok=True)
        db.set_setting('qq_meme_root', str(library))
        counts = catalog.import_pack(pack)
    else:
        counts = catalog.scan()
    return template, counts, registry


def run(args, client=None):
    from aicompanion.characters.registry import CharacterRegistry
    from aicompanion.pet.script import Vocab
    settings = None
    if client is None:
        if getattr(args, 'dotenv', None):
            # 只在用户显式指定时读取这份模型配置（例如在隔离快照里跑、配置在原项目目录）；不打印任何值
            from dotenv import load_dotenv
            load_dotenv(args.dotenv)
        from aicompanion.config import load_settings
        from aicompanion.providers import real_chat_client
        settings = load_settings()
        client, _ = real_chat_client(settings=settings)   # 需要真实模型；没在设置里配置时直接退出
    work = Path(args.out or ROOT/'output'/time.strftime('t14-eval-%Y%m%d-%H%M%S')).resolve()
    work.mkdir(parents=True, exist_ok=True)
    template, counts, registry = prepare_library(work, args.pack, settings)
    recorder = Recording(client)
    scenarios = load_scenarios(args.scenarios)
    if args.only: scenarios = [s for s in scenarios if s['id'] in set(args.only.split(','))]
    rows = []
    for round_no in range(1, args.rounds+1):
        for scenario in scenarios:
            path = work/f"{scenario['id']}-r{round_no}.sqlite3"; shutil.copyfile(template, path)
            db = Database(path); db.initialize()
            controller = ChatController(db, MemoryService(db), recorder); recorder.candidates = []
            registry_ = CharacterRegistry(db, settings, registry.packs.values(), environ={})
            character = next(c for c in db.characters() if c.id == WHALE); controller.character = character
            pack = registry_.pack(WHALE)
            if pack: controller.vocabs[WHALE] = Vocab.from_pack(pack)
            group = scenario.get('scene') == 'qq_group'
            channel = 'qq:group:900001' if group else 'desktop'
            for role, text in scenario.get('history', []):
                db.add_message(role, text, WHALE, source='qq' if group else 'text', channel=channel)
            # chain：同一段对话连续几轮（真实聊天里她发过的图会写回历史），用来看多轮下的表达习惯
            chain = scenario.get('chain'); steps = chain or [scenario]
            for n, step in enumerate(steps, 1):
                query = step.get('query', ''); proactive = step.get('proactive', '')
                if query:
                    if group: db.add_message('user', query, WHALE, source='qq', channel=channel)
                    else: controller.record_user_message(query)
                if hasattr(client, 'expect'): client.expect = step['expect']
                turn = f"eval-{scenario['id']}-{round_no}-{n}"
                context = ExpressionContext(turn, WHALE, 'qq:90009:group:900001' if group else 'desktop:'+WHALE,
                                            channel, 'qq' if group else 'pet', 'proactive' if proactive else 'user_reply', proactive,
                                            relationship=RelationshipSnapshot() if group else owner_relationship())
                situation = ('【场合】你在QQ群聊里，有人在叫你，群里其他人都看得到你的回复；每条消息前面是说话人的昵称和编号。'
                             '像群友一样回一两句，随意点，不用动作描写。') if group else ''
                events = []; started = time.monotonic(); error = ''
                try:
                    text = ''.join(controller.generate(turn, character, CancellationToken(), query, proactive, channel=channel,
                                                       scene='qq' if group else 'pet', situation=situation,
                                                       offer_tasks=False, expression_context=context, on_expression_event=events.append))
                except Exception as exc:   # 记录失败继续下一个场景
                    text = ''; error = type(exc).__name__+': '+str(exc)[:120]
                result = events[-1].result if events and events[-1].result else None
                attachment = result.attachments[0] if result and result.attachments else None
                # 模拟界面显示成功（这里不经过 Qt 界面），阶段记为已显示
                if attachment: controller.expression.receipt(attachment.delivery_id, 'displayed')
                trace = controller.expression.traces(1)[-1] if controller.expression.traces(1) else {}
                from aicompanion.pet.script import strip_cues
                rows.append(dict(id=scenario['id']+(f'#{n}' if chain else ''), round=round_no, group=scenario['group'], expect=step['expect'],
                                 input=query or ('（主动）'+proactive), reply=strip_cues(text, controller.vocabs.get(WHALE) or Vocab()),
                                 sticker=bool(attachment), meaning=attachment.alt_text if attachment else None,
                                 asset_id=attachment.asset_id if attachment else None,
                                 placement=attachment.placement if attachment else None,
                                 action=result.decision.action if result else 'error', candidates=trace.get('candidates'),
                                 stage=trace.get('stage'), diagnostics=trace.get('diagnostics'), rejected=trace.get('rejected'), error=error,
                                 seconds=round(time.monotonic()-started, 2), offered=list(recorder.candidates), human_verdict=''))
                print(f"{rows[-1]['id']:<22} r{round_no} {'图' if attachment else '字'} {rows[-1]['meaning'] or ''}", flush=True)
            db = None
    summary = summarize(rows)
    report = dict(kind='t14_expression_eval', date=time.strftime('%Y-%m-%d %H:%M'), real_model=not hasattr(client, 'expect'),
                  model=getattr(client, 'model', type(client).__name__), library=counts, pack=bool(args.pack),
                  synthetic_dialogue_only=True, summary=summary, rows=rows)
    (work/'expression-eval.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (work/'expression-eval.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('结果：', work/'expression-eval.md')
    return report


def summarize(rows):
    def rate(kind):
        part = [r for r in rows if r['expect'] == kind]
        return dict(total=len(part), sticker=sum(r['sticker'] for r in part),
                    rate=round(sum(r['sticker'] for r in part)/len(part), 3) if part else None)
    used = [r['asset_id'] for r in rows if r['asset_id']]
    stages = {}
    for r in rows: stages[r['stage']] = stages.get(r['stage'], 0)+1
    return dict(expressive=rate('expressive'), either=rate('either'), text=rate('text'),
                pure_sticker=sum(1 for r in rows if r['action'] == 'sticker_only'),
                distinct_images=len(set(used)), repeated_images=len(used)-len(set(used)),
                errors=sum(1 for r in rows if r['error']), stages=stages,
                note='发图率只是"是否过度保守"的专项观察，合不合适要看人工评估列。')


def markdown(report):
    s = report['summary']
    cell = lambda v: str(v or '').replace('|', '／').replace('\n', ' ⏎ ')
    lines = ['# T14 自然表达验收记录', '',
             f"- 时间：{report['date']}；真实模型：{report['real_model']}（{report['model']}）；图库：{report['library']}",
             f"- 适合发表情的场景：{s['expressive']['sticker']}/{s['expressive']['total']} 发了图；"
             f"两可：{s['either']['sticker']}/{s['either']['total']}；应纯文字：{s['text']['sticker']}/{s['text']['total']} 发了图",
             f"- 纯图回复 {s['pure_sticker']} 次；用到 {s['distinct_images']} 张不同的图，重复 {s['repeated_images']} 次；出错 {s['errors']} 次",
             '- 人工评估列请填：合适 / 图文不符 / 情绪不对 / 该发没发 / 不该发 / 人设不符', '',
             '| 场景 | 期望 | 输入 | 回复（⏎ 为换行） | 表情（位置） | 阶段 | 人工评估 |', '|---|---|---|---|---|---|---|']
    for r in report['rows']:
        image = f"{r['meaning']}（{'前' if r['placement'] == 'before_text' else '后'}）" if r['sticker'] else '—'
        lines.append(f"| {r['id']} r{r['round']} | {r['expect']} | {cell(r['input'])} | {cell(r['reply'] or r['error'])} | {cell(image)} | {r['stage']} | |")
    lines += ['', '<details><summary>每个场景模型看到的候选（判断"没发"是没合适的图还是没选）</summary>', '']
    for r in report['rows']:
        offered = '；'.join(x.split('｜')[1] for x in r.get('offered') or [] if '｜' in x)
        lines.append(f"- {r['id']} r{r['round']}：{offered or '（无候选）'}")
    lines += ['', '</details>']
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--pack', help='素材清单 manifest.json（不填则只用仓库自带 6 张图）')
    parser.add_argument('--scenarios', default=str(ROOT/'tools'/'expression_scenarios.json'))
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--only', help='只跑这些场景 id，逗号分隔')
    parser.add_argument('--out', help='输出目录（默认 output/t14-eval-时间戳）')
    parser.add_argument('--offline', action='store_true', help='脚本化假模型，只检查流程')
    parser.add_argument('--dotenv', help='从这个 .env 读取模型配置（只在你自己运行真实模型验收时用；不会打印密钥）')
    args = parser.parse_args(argv)
    run(args, ScriptedClient() if args.offline else None)


if __name__ == '__main__':
    main()
