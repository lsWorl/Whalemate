# V4-1：阶段一·地基——模型配置与按能力解析

依据：[项目计划书 v4](../project_plan_v4.md) 功能一、阶段一。负责：Claude 实现；Codex 审查；负责人 Windows 实机验收与合并。

里程碑：**能正常聊天**——在设置里配置模型和 Key 后能正常聊天、识图、查梗、办事；换 Key、换模型下一句就生效。

**2026-11-11 负责人决定：处于开发测试阶段，不兼容旧配置，直接重构；需要改表时直接删库重建。** 因此 `.env` 的 `LLM_*` 和角色包里的模型推荐都已移除。

## 分三个 PR

| PR | 内容 | 状态 |
| --- | --- | --- |
| V4-1a | `providers` 模块（账户 / 模型 / 能力绑定、凭据、按层解析、客户端缓存）、共用 `net.py` 禁止跳转、客户端支持供应商参数；`CharacterRegistry.client_for` 改由解析器实现 | 待审查 |
| V4-1b | 去掉旧配置（`LLM_*`、角色包模型推荐、`create_llm_client`、`controller.llm_client`）；调用点改为按“角色 + 能力 + 用途”取快照；办事子进程白名单环境变量；Breeze Blue 语音合成配置 | 进行中 |
| V4-1c | 模型设置页（账户与 Key、按能力选模型、角色单独指定、测试连接）＋阶段一实机测试手册 | 待开始 |

## 设计要点

- **两层查找**：用户为角色单独指定 → 用户全局设置。没有其他来源。聊天没配置时以离线模拟模式运行并提示；识图没配置就不看图；搜索没配置用维基百科；办事没配置不启用。配置存在 `app_settings` 的 `model_config` 一条 JSON 里，不改表结构。
- **快照**：每次请求解析出冻结的 `Resolved`，后台线程拿着它说完这一轮；换 Key、换模型只影响下一次请求。
- **缓存**：客户端按地址、模型、凭据修订和参数缓存；同地址同模型换 Key 也会换新实例。
- **凭据**：`env:NAME` 兼容旧 `.env`；`stored:NAME` 用 Windows DPAPI 加密后存入数据库。没有安全后端时拒绝保存，不退化成明文。界面只显示来源和是否可用，不显示 Key 的任何一部分。
- **思考策略**：按用途（聊天 / 分析 / 识图）翻译成供应商参数。DeepSeek 默认关闭思考以提速；MiniMax 始终分离推理，关不掉思考的模型给识图和分析留足输出额度。
- **不偷偷换服务**：配置的模型缺 Key 时报错提示，不退回别的收费服务；Harness 不认识配置的路由或模型时任务失败，不退回 DSH 默认模型。
- **办事子进程**：只传白名单里的系统变量和本次选定的 Key（DSH 内置多家供应商路由，父进程里的其他 Key 一个都不带进去）；换 Key 或路由时重启运行时。
- **测试与诊断**：`ChatController` 可直接收一个客户端（包成 `FixedRouter`），`use_client()` 供测试和桌宠自检临时替换；诊断脚本用 `providers.real_chat_client()` 读取设置里的模型。
- **复用**：项目里原有三份 `NoRedirect` 合并为 `aicompanion/net.py`；聊天和语音客户端也改用它。

## 参考

- 锁定版 DeepSeek Harness 0.2.1rc2 内置的供应商路由（`deepseek-official`、`minimax`、`minimax-cn`）及其环境变量名，Agent 目标直接沿用。
- 官方文档：[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[MiniMax OpenAI 接口](https://platform.minimax.io/docs/api-reference/text-openai-api)。
