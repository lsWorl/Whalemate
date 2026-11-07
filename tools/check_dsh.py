"""真实模型小任务（会计费），只用进程环境凭据和本次临时文件；不读取 .env。"""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from aicompanion.agent.dsh_backend import DshAgent, problem
from aicompanion.agent.tools_server import Task, ToolBox
from aicompanion.agent.mcp_server import McpToolServer, dependency_problem
from test_dsh_live import credentials, redact, temporary_dsh_home

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output'/'dsh-check'


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    key, base = credentials()
    report = {'passed': False, 'done': False, 'cases': [], 'settings_source': 'process-environment-only'}
    issue = '未提供进程环境 API 凭据，未读取 .env，未执行真实模型测试。' if not key else problem() or dependency_problem()
    if issue:
        report.update(skipped=True, reason=issue)
        code = 2
    else:
        try:
            with ExitStack() as stack:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='fixture-', dir=OUT)))
                workspace = root/'workspace'; workspace.mkdir()
                (workspace/'a.txt').write_text('hello', encoding='utf-8')
                (workspace/'b.md').write_text('# hi', encoding='utf-8')
                home = stack.enter_context(temporary_dsh_home())
                stack.enter_context(patch('aicompanion.agent.dsh_backend.dsh_home', return_value=home))
                box = ToolBox(secrets=[home], trash=root/'trash'); box.task = Task(allowed=[workspace])
                asked = []
                box.approve = lambda kind, title, detail: (asked.append(kind), 'no')[1]
                server = McpToolServer(box); stack.callback(server.close)
                agent = DshAgent(); stack.callback(agent.close); agent.mcp_servers = [server.acp_entry()]
                start = time.monotonic()
                result = agent.run(f'你是鲸娘，回复用一两句中文。只用 aicompanion 工具做事。当前工作区 {workspace} 只有两个合成文件。'
                                   '统计文件按扩展名各有几个，用 write_file 写到 stats.txt，不使用命令、不访问其他路径。', workspace,
                                   api_key=key, base_url=base)
                stats = workspace/'stats.txt'
                report.update(done=True, status=result.status, seconds=round(time.monotonic()-start, 2),
                              text=result.text, usage=vars(result.usage), asked=asked,
                              stats=stats.read_text(encoding='utf-8') if stats.exists() else '')
                report['passed'] = result.status == 'done' and stats.is_file() and not asked
        except Exception as error:
            report['passed'] = False
            report['error'] = str(error)
        code = 0 if report['passed'] else 1
    text = redact(json.dumps(report, ensure_ascii=False, indent=2), key)
    (OUT/'report.json').write_text(text, encoding='utf-8')
    print(text)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
