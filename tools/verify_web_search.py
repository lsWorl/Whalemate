"""T20 真实联网搜索 / 读页验收：用项目已配置的聊天接口跑几次真实搜索，看能不能搜到、搜到的和这个词有没有关系、要多久。

T18 的搜索和读页只用假接口测过（负责人 2026-11-10 转来的缺口清单 P1）。这个脚本由用户自己运行：
- 走和聊天里查梗完全相同的代码（community/lookup.py 的 Lookup.find：搜索 → 只留提到这个词的结果 → 摘要太短就读页）；
- DeepSeek 官方接口时用 DeepSeek Search / Fetch（按次计费，每 1000 次成功请求约 0.75 美元）；其他接口退回中文维基；
- 只用临时数据库，不读写真实数据库；只打印后端、条数、耗时、标题和站点名，不打印密钥和请求头。

用法（Windows，项目根目录）：
  .venv\\Scripts\\python.exe tools\\verify_web_search.py
  .venv\\Scripts\\python.exe tools\\verify_web_search.py --terms 依托答辩,哈基米,典中典 --dotenv D:\\Study\\AICompanion\\.env
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from aicompanion.community.lookup import Lookup, relevant   # noqa: E402
from aicompanion.community.search import WebSearch          # noqa: E402
from aicompanion.storage.database import Database           # noqa: E402

DEFAULT_TERMS = '依托答辩,哈基米,典中典,绷不住了,鼠鼠我啊'


def run(args, client=None, web=None):
    if client is None:
        if getattr(args, 'dotenv', None):
            from dotenv import load_dotenv
            load_dotenv(args.dotenv)
        from aicompanion.config import load_settings
        from aicompanion.providers import real_chat_client
        client, _ = real_chat_client(settings=load_settings())
    with tempfile.TemporaryDirectory() as folder:
        db = Database(Path(folder)/'verify.sqlite3'); db.initialize()
        web = web or WebSearch(db, client)
        look = Lookup(db, client, web=web)
        rows = []
        for term in [t.strip() for t in args.terms.split(',') if t.strip()]:
            started = time.monotonic(); raw = web.search(f'{term} 梗 意思', 5); first = round(time.monotonic()-started, 2)
            started = time.monotonic(); hits = look.find(term); total = round(time.monotonic()-started, 2)
            rows.append(dict(term=term, raw_results=len(raw), raw_relevant=len(relevant(term, raw)), search_seconds=first,
                             usable=len(hits), lookup_seconds=total, top=[f'{h.title}（{h.site or "?"}）{len(h.summary)}字' for h in hits[:2]]))
            print(f"{term:<10} 原始 {len(raw)} 条 / 相关 {rows[-1]['raw_relevant']} 条 / 可用 {len(hits)} 条  搜索 {first}s 整个查梗 {total}s  {'；'.join(rows[-1]['top'])}", flush=True)
        used = web.used_today() if hasattr(web, 'used_today') else {}
        found = sum(1 for r in rows if r['usable'])
        report = dict(kind='t20_web_search_verify', date=time.strftime('%Y-%m-%d %H:%M'), backend=web.backend(), terms=len(rows), found=found,
                      requests=used, slowest_lookup_seconds=max((r['lookup_seconds'] for r in rows), default=0), rows=rows,
                      passed=bool(rows) and found >= max(1, len(rows)//2))
        db = None
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, ensure_ascii=False, indent=2))
    if args.out: Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--terms', default=DEFAULT_TERMS, help='要查的词，逗号分隔（公开的网络用语，不要放群友昵称）')
    parser.add_argument('--out', help='把结果写成 JSON 文件')
    parser.add_argument('--dotenv', help='从这个 .env 读取模型配置（不会打印密钥）')
    args = parser.parse_args(argv)
    sys.exit(0 if run(args)['passed'] else 1)


if __name__ == '__main__':
    main()
