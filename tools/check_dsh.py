"""DeepSeek Harness 实测：在临时工作区里让她跑一个小任务（统计文件 + 写一个文件），用桌宠自己的工具，记录耗时、用量和结果。
读取 .env 里的 DeepSeek 接口，会产生少量词元用量。需要确认的步骤一律自动拒绝。结果写到 output/dsh-check/report.json。"""
import json
from pathlib import Path
import tempfile
import time

from aicompanion.agent.dsh_backend import DshAgent, problem
from aicompanion.agent.tools_server import Task, ToolBox
from aicompanion.agent.mcp_server import McpToolServer
from aicompanion.config import load_settings

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output'/'dsh-check'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'problem': problem()}
    if not report['problem']:
        settings = load_settings()
        workspace = Path(tempfile.mkdtemp(prefix='aic-dsh-check-'))
        (workspace/'a.txt').write_text('hello', encoding='utf-8'); (workspace/'b.md').write_text('# hi', encoding='utf-8')
        box = ToolBox(secrets=[ROOT/'.env', settings.database_path]); asked = []
        box.approve = lambda kind, title, detail: (asked.append(title), 'no')[1]
        box.task = Task(run_id=0, allowed=[workspace]); server = McpToolServer(box)
        agent = DshAgent(); agent.mcp_servers = [server.acp_entry()]
        start = time.monotonic()
        result = agent.run(f'你是鲸娘，回复用一两句俏皮的中文口语。用 aicompanion 的工具做事。当前工作区：{workspace}。'
                           '用户的原话：帮我统计一下当前文件夹里的文件按扩展名各有几个，再把统计结果写进 stats.txt。', workspace,
                           api_key=settings.llm_api_key, base_url=settings.llm_base_url)
        agent.close(); server.close()
        report.update(seconds=round(time.monotonic()-start, 1), status=result.status, text=result.text, steps=[s[1] for s in result.steps],
                      usage=vars(result.usage), asked=asked, stats=(workspace/'stats.txt').read_text(encoding='utf-8') if (workspace/'stats.txt').exists() else '')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
