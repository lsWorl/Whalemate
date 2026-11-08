# 任务清单

状态：待开始 / 进行中 / 待审查 / 已合入。开工前把自己的名字填上；每个任务的说明在 `docs/tasks/`。

| 编号 | 任务 | 负责 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| T1 | 协作规则、`.gitattributes`、任务清单 | Claude | 已合入 | PR #1 |
| T2 | MCP 服务改用官方 `mcp` 包（统一接口） | Codex | 待复审（PR #4） | `docs/tasks/T2-mcp-official-sdk.md`；工作区隔离与行尾断言已修复，Codex 真实 DeepSeek 六类 / 八任务全通过，246 项离线检查通过 |
| T11 | 工具把关安全修复：命令只读误判、移动文件时目标目录的权限检查、停止任务时正在跑的命令要一起停 | Claude | 已合入 | PR #3，保留原任务绑定及完整进程树停止 |
| T3 | 外部 MCP 服务接入（经我们的服务转发，统一把关） | Claude | 待开始（依赖 T2、T11） | 待写 |
| T4 | 应用内确认气泡真机走查（工作区外写、删除、命令） | Claude + 用户 | 待开始 | 需要用户在场点击 |
| T5 | P8 美术：复用已确认鲸娘的十动作与分层素材 | Codex | 源图层已合入（PR #5）；补画 / 支点待完成 | `docs/tasks/T5-character-art.md`；17 状态独立回拼零差异，保留原设计及标志 |
| T6 | P8 代码：十姿态分层包的编译与渲染，接入已确认鲸娘 | Claude | 代码已合入（PR #2）；观感待用户验收 | `docs/tasks/T6-rig-studio.md` 第 A 节已确认；原 1254 画布、十姿态与小幅运动 |
| T7 | 内存优化（目标 ≤ 350 MB） | 待定 | 待开始 | `tools/measure_pet.ps1` |
| T8 | P4 外放场景验收、连续 10 分钟对话 | 用户 + 待定 | 待开始 | |
| T9 | P7 QQ / B站 | Claude 实现，Codex 方案与代码审查 | 第一版（PR #6）、一键接入（PR #7）已合入；QQ 回复已真机可用；人设 v3 与群里主动聊天进行中（`task/t9-persona-group`） | `docs/tasks/T9-astrbot-bridge.md`；QQ 采用 AstrBot + NapCat + 桌宠大脑桥接；绑定用户（她叫爹）、全链路场景隔离、离线不切第二人格 |
| T9-诊断 | QQ 点击启动后仍显示 stopped 的真机排查 | Codex | 已合入（PR #8）；用户确认 BOM 修复后恢复已连接 | 重试、保存配置和准备组件错误持续保留并脱敏 |
| T10 | 开发日志与项目页 | — | 项目完结后 | 草稿已在 MyBlog `_drafts` |
| P0-ARCH | 五项长期架构约束入库 | Codex | 待复审（纯文档） | docs/tasks/P0-architecture-constraints.md |
