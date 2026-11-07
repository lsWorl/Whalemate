# 任务清单

状态：待开始 / 进行中 / 待审查 / 已合入。开工前把自己的名字填上；每个任务的说明在 `docs/tasks/`。

| 编号 | 任务 | 负责 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| T1 | 协作规则、`.gitattributes`、任务清单 | Claude | 已合入 | PR #1 |
| T2 | MCP 服务改用官方 `mcp` 包（统一接口） | Codex | 进行中（PR #4 审查修正） | `docs/tasks/T2-mcp-official-sdk.md`；修正关闭、依赖与原 Task 绑定 |
| T11 | 工具把关安全修复：命令只读误判、移动文件时目标目录的权限检查、停止任务时正在跑的命令要一起停 | Claude | 待审查 | PR #3 |
| T3 | 外部 MCP 服务接入（经我们的服务转发，统一把关） | Claude | 待开始（依赖 T2、T11） | 待写 |
| T4 | 应用内确认气泡真机走查（工作区外写、删除、命令） | Claude + 用户 | 待开始 | 需要用户在场点击 |
| T5 | P8 素材：**只做已确认的原鲸娘十动作**（保留原设计和标志；Claude、ChatGPT 形象暂缓），整理 `pose-layered-v2` 进 `art/deepseek-whale/` 并按需补画 | Codex | 进行中（PR #5） | `docs/tasks/T6-rig-studio.md` 第 A 节（待确认） |
| T6 | P8 代码：十姿态分层包的编译与渲染，替换借用的大肥鱼 | Claude | 等 Codex 确认第 A 节契约（PR #2） | `docs/tasks/T6-rig-studio.md` |
| T7 | 内存优化（目标 ≤ 350 MB） | 待定 | 待开始 | `tools/measure_pet.ps1` |
| T8 | P4 外放场景验收、连续 10 分钟对话 | 用户 + 待定 | 待开始 | |
| T9 | P7 QQ 等聊天平台：**不自己写适配器**，用 AstrBot + NapCat 接平台，写一个 AstrBot 插件把消息转给桌宠的大脑（人格和记忆只有一份） | Claude 实现，Codex 方案与代码审查 | 最后再做（P8 之后） | `docs/tasks/T9-astrbot-bridge.md` |
| T10 | 开发日志与项目页 | — | 项目完结后 | 草稿已在 MyBlog `_drafts` |
