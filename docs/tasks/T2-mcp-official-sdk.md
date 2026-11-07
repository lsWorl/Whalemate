# T2：MCP 服务改用官方 `mcp` 包

负责：Codex　审查：Claude　分支：`task/t2-mcp-sdk`

## 背景

干活时 DeepSeek Harness（DSH，ACP 模式）只负责想；读写文件、删除、命令都通过我们自己的 MCP 服务执行，执行前把关。
现在的服务是 `src/aicompanion/agent/tools_server.py` 里手写的 `ToolServer`（只回 JSON 的简化 Streamable HTTP）。
目标：换成官方 Python SDK，统一 MCP 接口，为 T3（接入外部 MCP 服务并统一把关）打基础。

## 要做

1. 依赖：`pyproject.toml` 的 `agent` 组加 `mcp`，锁到 2.3.x（2.3.0，2026-10-02 发布，Python ≥ 3.10）；`tools/setup_dsh.bat` 同步安装。
   注意 2.x 把服务端类从 `FastMCP` 改成了 `MCPServer`，按 2.x 文档写（https://py.sdk.modelcontextprotocol.io/ 、迁移指南、API 参考）。
2. 新建 `src/aicompanion/agent/mcp_server.py`：
   - 用 `MCPServer` 注册和现在 `ToolBox.TOOLS` 同名、同参数的 10 个工具：list_dir、glob、grep、read_file、write_file、edit_file、make_dir、move_path、delete_path、run_powershell。参数用类型标注生成输入格式，说明文字沿用现在的中文描述。
   - 每个工具**写成 async**，用 `anyio.to_thread.run_sync(box.call, name, args)` 调 `ToolBox`。原因：`ToolBox` 会阻塞等用户点确认气泡（最长 2 分钟），同步工具会卡住事件循环，其他调用全部挂起。
   - `ToolBox.call` 返回 `(text, is_error)`：`is_error` 为真时按 MCP 规范返回 `isError: true` 的结果（不要抛成协议错误，模型需要看到"被拒绝"的文字）。
   - 传输：Streamable HTTP，只监听 `127.0.0.1`，端口由系统分配；在后台线程里起事件循环和 HTTP 服务，`close()` 能干净退出。
   - 认证：保留一次性 Bearer 令牌（`Authorization` 头），没有令牌或不对的请求返回 401。
   - 对外接口保持不变，供 `ui/agent_session.py` 直接替换：`McpToolServer(box)`，属性 `url`、`token`，方法 `acp_entry()`（返回 `{'type': 'http', 'name': 'aicompanion', 'url': ..., 'headers': [{'name': 'Authorization', 'value': 'Bearer ...'}]}`）、`close()`。
3. `ToolBox`（规则和工具实现）**不改**；删除旧的 `ToolServer` 类，`agent_session.py`、`tools/check_dsh.py`、`tools/test_dsh_live.py`、`ui/pet_smoke_check.py` 改用新类。`ToolBox.TOOLS` 里的 JSON 结构如果已无人使用可以删。

## 验收

- `tests/test_dsh.py` 里 `ToolBoxTest` 的用例**一条不改**，全部通过。
- 原来的 `test_mcp_server_round_trip` 改为用官方 SDK 的客户端连新服务：初始化、列出 10 个工具、调用 read_file、错误令牌被拒。再加一条：一个工具在等确认时（`approve` 阻塞 2 秒），另一个只读工具能立刻返回。
- `tools\run_checks.bat` 通过（单元测试 + 桌宠自检，自检里 `tools_listed: 10`、`env_blocked`、`tools_gate` 为 true）。
- `tools\run_agent_checks.bat` 六个真实场景结果和现在一致（见 `docs/implementation_status.md` "干活改为 DSH 想、桌宠动手" 一节）。
- 用 `tools\measure_pet.ps1` 复测内存，在 PR 里写明前后数字（新依赖会带进 pydantic、starlette、uvicorn、httpx 等）。

## 不要做

- 不要把外部 MCP 服务直接交给 DSH（那样会绕过把关）——这是 T3 的事。
- 不改 `ToolBox` 的放行规则；如果觉得规则有问题，在 PR 里提出来。
- 不读 `.env`、不提交 `data/` 和 Coopanion 相关文件。

## 当前实现范围（Codex）

- `pyproject.toml` 仅修改 `agent` 依赖组，并增加独立的 `requirements-agent-lock.txt`；不修改 T6 的素材工具依赖。
- 服务按需在后台启动，未安装 agent 可选依赖时聊天和桌宠仍可启动；第一次实际干活时才启动官方服务。
- `ui/pet_smoke_check.py` 使用官方客户端在 QThread 中检查旧协议握手、列工具；自检不调用真实回收区清理，也不加载私人面板背景。
- T11 的执行与取消规则来自已合入的 PR #3；本分支同步最新协作基线，T2 不修改 ToolBox 的放行规则。

## 本轮审查修正

- 关闭等待超时不再抛异常中断应用退出：记录日志与 `shutdown_problem`，返回 False 并保留线程引用，可再次等待清理；正常退出返回 True。
- 启动前同时检查 mcp、uvicorn、httpx2、anyio；不为依赖提示提前导入 SDK。
- `_wait_ui` 优先绑定 T11 的 `box.current`；原任务取消后不弹出新的确认，也不误用新任务的取消状态。同步 PR #3 后继续保留这一行为。
- 明确 HTTP 取消只放弃等待，实际工具停止仍依赖所属 Task.cancelled；没有把中断请求写成能杀死 Python 线程。
- 两个真实模型脚本只读取进程环境凭据，所有合成文件、假密钥、回收区及 DSH 会话都放在新建临时目录；不读取本机 .env，不访问 MyBlog，不清理以前的测试目录。
- `run_agent_checks.bat` 传播失败/未执行的退出码；缺少测试凭据时写 skipped=true、passed=false 并退出 2。`setup_dsh.bat` 只安装锁定依赖和 pip check，收费验收由用户单独调用。
- 以上没有增加依赖，也没有修改 config.py、数据库表或 T11 的工具放行规则。真实模型六类场景（8 次任务）必须在已提供进程环境凭据时运行；缺少凭据不能计为通过。

## 已合入基线的整合

- 同步包含 PR #2、#3、#5 的 `collaboration-baseline`，保留 T6 的 studio 可选依赖及鲸娘源素材、运行时，不将其重复归为 T2 的修改。
- tools_server.py 保留 T11 完整的 `_kill_tree`（Windows taskkill /T、POSIX killpg、kill / wait 兜底），删除旧 ToolServer；保留 T11 的精确 Git 选项允许列表、原任务绑定及写入审批。
- 增加真实 MCP 客户端 → SDK 工作线程 → ToolBox → AgentSession 审批的组合回归：旧审批挂起时启动新任务并取消旧任务，旧写入必须失败，新任务保持可用。
- 已实测真实 DSH 新会话发现官方 MCP 并取得全部 10 个工具（没有发送模型提示）。但用户后续收费验收在 D 盘测试 DSH_HOME 的 session/new 阶段失败，Claude 已撤回可合并标记；修复后须重新验证六类场景，不能沿用此前的延后结论。

## session/new 验收目录修复

- 两个验收入口复用 temporary_dsh_home：在 sandbox.low_root() 下每次新建独立临时会话目录，运行时与正式 DSH_HOME 使用同一盘；工作区、合成文件与回收区继续留在项目 output 的临时 fixture，覆盖 D 盘 exFAT 场景。
- 先结束 DSH 与 MCP 服务，再恢复 patch、清理本次临时目录；不触及正式会话。临时会话目录加入 ToolBox 的 secrets，禁止工具读取验收会话内容。
- 离线回归覆盖两个实际入口都选择 low_root 所在盘及其直接子目录、异常后清理与既有目录保留。真实运行时对照验证到 session/new，而非仅 initialize；不修改正式 agent/ 代码，stderr 增强仍归 Claude 的 T12。
- 最新可合并结论须等 Claude 复审及修复后的真实模型验收；不把无提示的 session/new 验证记作八次模型任务通过。
