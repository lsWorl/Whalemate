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
