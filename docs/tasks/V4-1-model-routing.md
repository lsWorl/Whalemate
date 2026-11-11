# V4-1：阶段一·地基——模型配置与按能力解析

依据：[项目计划书 v4](../project_plan_v4.md) 功能一、阶段一。负责：Claude 实现；Codex 审查；负责人 Windows 实机验收与合并。

里程碑：**能正常聊天**——升级后各角色用的模型与以前一样，可以在设置里换 Key、换模型，换完下一句就生效。

## 分三个 PR

| PR | 内容 | 状态 |
| --- | --- | --- |
| V4-1a | `providers` 模块（账户 / 模型 / 能力绑定、凭据、按层解析、客户端缓存）、共用 `net.py` 禁止跳转、客户端支持供应商参数；`CharacterRegistry.client_for` 改由解析器实现 | 待审查 |
| V4-1b | 调用点改为按“角色 + 能力 + 用途”取快照：ChatController、QQ 识图、查梗、记忆 / 关系 / 社区分析、目的提炼、日语翻译、搜索、办事；去掉对 `controller.llm_client` 的写入；用量按配置计价 | 待开始 |
| V4-1c | 模型设置页（账户与 Key、按能力选模型、角色单独指定、测试连接）＋阶段一实机测试手册 | 待开始 |

## 设计要点

- **四层查找**：用户为角色单独指定 → 用户全局设置 → 角色包推荐（读环境变量）→ 旧 `.env`。前两层为空时与升级前完全一样，所以不搬数据、不改表结构；用户配置存在 `app_settings` 的 `model_config` 一条 JSON 里。
- **快照**：每次请求解析出冻结的 `Resolved`，后台线程拿着它说完这一轮；换 Key、换模型只影响下一次请求。
- **缓存**：客户端按地址、模型、凭据修订和参数缓存；同地址同模型换 Key 也会换新实例。
- **凭据**：`env:NAME` 兼容旧 `.env`；`stored:NAME` 用 Windows DPAPI 加密后存入数据库。没有安全后端时拒绝保存，不退化成明文。界面只显示来源和是否可用，不显示 Key 的任何一部分。
- **思考策略**：按用途（聊天 / 分析 / 识图）翻译成供应商参数。DeepSeek 默认关闭思考以提速；MiniMax 始终分离推理，关不掉思考的模型给识图和分析留足输出额度。
- **不偷偷换服务**：用户指定的模型缺 Key 时报错提示，不退回别的收费服务。只有角色包推荐这一层沿用旧行为（缺专属 Key 时用默认模型并在标签里说明）。
- **复用**：项目里原有三份 `NoRedirect` 合并为 `aicompanion/net.py`；聊天和语音客户端也改用它。

## 参考

- 锁定版 DeepSeek Harness 0.2.1rc2 内置的供应商路由（`deepseek-official`、`minimax`、`minimax-cn`）及其环境变量名，Agent 目标直接沿用。
- 官方文档：[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[MiniMax OpenAI 接口](https://platform.minimax.io/docs/api-reference/text-openai-api)。
