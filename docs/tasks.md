# 任务清单

状态：待开始 / 进行中 / 待审查 / 已合入。开工前把自己的名字填上；每个任务的说明在 `docs/tasks/`。

| 编号 | 任务 | 负责 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| T20 | 原鲸娘自绘补帧与流畅动画 | Codex | 草稿待审图 / 审码（Codex，PR #25；20 动作 / 36 段过渡，低步幅二维行走；699 通过 / 7 跳过） | docs/tasks/T20-original-whale-animation.md；用户撤回 #22 的外观替换，保留原设计，借鉴逐帧 / 后台解码；不引入 Aboutme 图片或声音 |
| T14 | 智能表情正式开发：资产、同次生成、桌面与 QQ | Claude 实现；Codex 审计 | 已合入（PR #13）；负责人真机试用中 | docs/tasks/T14-smart-expression-design.md |
| T17 | 收集别人发的表情包、向群友学发图习惯、聊天短句分条 | Claude 实现；Codex 审计 | 已合入（PR #16、#17、#18） | docs/tasks/T17-sticker-learning.md |
| T15 | 自主社交人格与人物关系系统 | Claude 实现；Codex 审查 | 进行中（Claude，`task/t15-relationship`） | docs/tasks/T15-relationship.md；人物身份、好感 / 熟悉 / 信任、关系事件确定性打分、自主意愿进同一个大脑、人物关系页；schema 6→7；`ui/settings_window.py` 只加一行挂页面 |
| T18 | 社交语境与攻击意图理解、社区知识学习（黑话 / 梗、来源、演变、联网）、自然串梗与社区参与 | Claude 实现；Codex 审查 | 进行中（Claude，`task/t18-social-community`，叠在 T15 之上） | docs/tasks/T18-social-community.md；schema 7→8；插件 0.9.5；`ui/settings_window.py` 只加一行挂页面 |
| T14-导入修复 | 素材清单导入无反馈：线程清理与结果提示 | Codex | 修复已验证，负责人授权追加至 PR #13，待 Claude 复核与用户重启验收：专项 38；全量 560（553 通过 / 7 跳过）；Smoke 通过；171 张两次实导入通过 | 仅图库界面、合成测试和 Smoke；不改用户库、主窗口和表结构 |
| T14-QQ诊断 | AstrBot 被 Windows 应用控制拦截及旧日志混入 | Codex | 诊断已修复并验证；真实 QQ 仍阻塞：系统应用控制拦截未签名解释器；不改变系统策略 | 运行时受信任发布仍需解决，不能标记 QQ 恢复 |
| T14-0 | 智能表达协议与接入设计冻结（历史） | Codex；Claude 复审 | 负责人已授权进入 T14 正式开发 | docs/tasks/T14-smart-expression-design.md 已同步实际实现；最新正式指令要求旧标签统一适配，取代 v1 的直接移除策略；原设计保留 |
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
| T9-应用控制 | QQ 组件被 Windows 智能应用控制拦截（4551） | Claude 实现，Codex 审查 | 已合入（PR #15） | AstrBot 改由基础解释器启动；Windows 临时目录实测可完整启动、0 拦截；真实 QQ 登录与全新安装流程待验证 |
| T10 | 开发日志与项目页 | — | 项目完结后 | 草稿已在 MyBlog `_drafts` |
| T13 | 可选择记忆和表情包存放磁盘 | Codex | 已合入（PR #11，合并提交 `d2ff507`） | 默认空图库中文分类 / 用户自建分类集成回归；21 存储专项，全量 408（401 通过 / 7 跳过）及 Smoke 通过；P3 决定见任务；T13 先合入、T14 后同步，不改 T14 分支 |
| P0-ARCH | 五项长期架构约束入库 | Codex | 已合入（PR #12） | docs/tasks/P0-architecture-constraints.md |
| T12 | QQ 识图、自然表情与社区表达学习 | Codex | 已合入（PR #10，合并提交 `2e8ee11`）；新风格待真机体验 | 用户确认私聊表情已成功识别；新增聊天卡和带来源社区词义，表情只理解含义、不展开分析；387 项检查通过，真实模型短句 / 原创表情复测通过 |
| T19 | 对外人更像真人：外人使唤她办事时按真人的方式回（问梗时顺手搜一下）、和外人聊尽量不用标点 | Claude 实现；Codex 审查 | 待审查（Claude，`task/t19-human-outsiders`，叠在 T18 上） | docs/tasks/T19-human-outsiders.md |
| T20 | 补 T18 的缺口：当场查没见过的梗并记进词库、查不查用不用由她决定、真实搜索验收脚本、小圈子来源、语境对照评测 | Claude 实现；Codex 审查 | 待审查（Claude，`task/t20-community-gaps`，叠在 T19 上） | docs/tasks/T20-community-gaps.md |
| T21 | 预置词库：B 站、游戏、二次元、抽象圈的常用梗 336 个，首次启动装入；附 T18–T21 真机测试手册 | Claude 实现；Codex 审查 | 待审查（Claude，`task/t21-seed-lexicon`） | docs/tasks/T21-seed-lexicon.md |
| T22 | 说了不理就真的不理、按好感分三档决定回几条回不回、回得更像真人、和外人聊不用逗号、预置词库第 2 版（二游 / 米哈游 / 抽象 +167） | Claude 实现；Codex 审查 | 待审查（Claude，和 T21 同在 `task/t21-seed-lexicon`） | docs/tasks/T22-outsider-stance.md |
