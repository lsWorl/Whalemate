# 任务清单

状态：待开始 / 进行中 / 待审查 / 已合入。开工前把自己的名字填上；每个任务的说明在 `docs/tasks/`。

| 编号 | 任务 | 负责 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| V4-QQ-本地 | 恢复新装 QQ 的 NapCat 来源，验证自包含和扫码前链路 | Codex 实现，Claude 审查 | 待审查，最终包已验至二维码 | [任务说明](tasks/V4-qq-local-napcat.md) |
| V4-5 | 真正退出自己的程序，保存告别原因并在重启后接上 | Codex 实现、Claude 审查 | 首轮 P2、P3 已修，待 Claude 复审（PR #53） | [任务说明](tasks/V4-5-self-exit.md) |
| V4-发布 | 解压即用的 Windows 压缩包（AICompanion.exe + UninstallWhalemate.exe，卸载清除程序、数据库和全部本机数据）、上传私有仓库 Releases、README 增加下载安装与卸载说明 | Codex 实现与打包；Claude 审计、上传、README | PR #52 P1/P2 已修复，待 Claude 复审；最终 ZIP 的主程序被本机应用控制拦截，两个打包 smoke 和运行中卸载待验 | docs/tasks/V4-release.md |
| V4-UI | 界面重构：鲸鲸主题、聊天面板、桌宠右键菜单分组、首次启动向导、设置换装并改为自动保存（让非技术用户也能用） | Codex 实现；Claude 审计 | 已处理第二轮审计，待 Claude 复审（#51，`task/v4-ui-redesign`） | docs/tasks/V4-UI-redesign.md；设计稿 docs/design/v4-ui/ |
| T20-动画 | 鲸鲸默认播放本机安装的 Aboutme 大肥鱼，缺包时使用内置自绘鲸娘；分层源稿可从 `fa54555` 找回 | Codex | 独立审查后修订，待复核（PR #25） | [任务说明](tasks/T20-original-whale-animation.md) · [安装与署名](aboutme-pet.md) |
| OSS-文档清理 | 负责人 2026-11-12 要求删除过时文档：计划书 v1～v3、T14 原始方案、`docs/tasks/evidence/` 下的 T14 评测证据；引用处改为指向 git 历史（提交 `7ebc39c`） | Claude 文档；Codex 审查 | 已合入（PR #50，`82eaeee`） | 旧自绘鲸娘的运行素材按 #25 的回退需求保留 |
| OSS-Live2D-Core | Live2D Cubism Core（专有许可）从仓库和打包版本中移除：用户按需运行 `tools/fetch_avatar.py` 下载；缺少时导入提示获取方法、启动退回自带形象；`.gitignore`、wheel 和 `build_windows.py` 都排除 Core | Claude 实现；Codex 审查 | 待审查（`task/oss-live2d-core`，叠在 #44 上） | 负责人 2026-11-11 要求；动到 `ui/main_window.py`、`pyproject.toml`（共享文件，改动见 PR） |
| OSS-开源准备 | 项目取名 Whalemate · 鲸伴；重写中文主 README 并新增英文版 README.en.md；致谢参考过的开源项目与大肥鱼社区；新增 MIT LICENSE（Live2D Cubism Core 等第三方组件除外） | Claude 文档；Codex 审查；负责人确认 | 待审查（`task/oss-readme`） | 美术素材按 MIT（负责人 2026-11-11 决定）；是否改包名 / 仓库名待定 |
| PR42-冲突 | 合入当前目标分支 #41，保留关系设定与声线修复记录 | Codex | 已解决（功能审计另行记录） | 仅 Markdown 冲突；功能审计另行记录 |
| PR41-冲突 | 合入最新协作基线，保留默认声线与群聊实施记录 | Codex | 已解决（功能审计另行记录） | 仅 Markdown 冲突；功能审计结果另行记录 |
| PR40-冲突 | 接入已合并 PR #39 的基线，保留语音与群聊两份实施记录 | Codex | 已解决（负责人决定合并 PR） | 仅 Markdown 冲突；负责人明确不做回归测试 |
| V4-7a | 约定识别、分渠道到期提醒与设置 | Codex 实现、Claude 审查 | 待审查 | [任务说明](tasks/V4-7a-promises.md) |
| V4-8c | 长回复附带口播版 | Codex 实现、Claude 审查 | 两项 P2 已修，待 Claude 复审（#58） | [任务说明](tasks/V4-8c-say.md) |
| V4-8 回复带语音 | 计划书 v4 功能八：回复默认带语音、长回复分段取舍、大肥鱼声线。**先修 Codex 在 #38 实机发现的基线问题**：已经 complete 且展示完的回复，被下一轮 submit 或退出时 `_end_call → interrupt` 改成 cancelled，重启后显示“被打断”，也会让全文索引（只查 complete）漏掉这些旧消息。实机先验证系统朗读回退；云端声线（音色试听）未配置前不算完成 | Claude 实现；Codex 审查 | 待审查（第一版，`task/v4-8-voice`，从 #38 的 6775d81 拉出） | docs/tasks/V4-8-voice.md |
| V4-8b 鲸鲸默认声线 | 负责人 2026-11-11：鲸鲸默认用网上 AI 视频那种有感情的少女声线。角色声线设定（`domain/voice_profile.py`）；MiniMax 语音客户端（和聊天共用 MiniMax 账户）；没在模型页配语音时用角色默认声线，候选音色在设置里换；心情明显时指定情绪，其余交给模型按台词自动选。音色听感由负责人试听拍板 | Claude 实现；Codex 审查 | 待审查（`task/v4-8b-whale-voice`，叠在 #39 之上） | docs/tasks/V4-8b-whale-voice.md |
| V4-2b 关系设定 | 负责人 2026-11-11：人设不要写死父女（女朋友、妹妹都行），项目是情感框架。人格包加“和你的关系”（预设 + 自定义），提示词、QQ 群标记、冒充检测、聊天卡、办事人设都按当前关系；记忆统一写“用户” | Claude 实现；Codex 审查 | 待审查（`task/v4-2b-bond`，当前叠在 #41 之上；Codex 首轮 2 个 P2 已修，待复核） | docs/tasks/V4-2b-bond.md |
| V4-9b | 纪念日提前提醒 | Codex 实现、Claude 审查 | 待审查 | docs/tasks/V4-9b-date-reminders.md |
| V4-9 陪伴日期（基础版） | 计划书 v4 功能九：认识第几天（按日历日）、聊过几天、纪念日（第 100 天、周年、她的生日、办成事周年、公历和农历节日）、用户添加生日和纪念日（可按农历）；私下聊天时当天提一句、一天只提一次；群里不带；设置里“我们的日子”；schema 11→12；新依赖 lunar_python==1.4.9（负责人同意）。提前提醒、聊天里确认生日留到后续 | Claude 实现；Codex 审查 | 代码审查及 Windows 核心界面走查通过（临时数据 / 模拟回复）；1 项 P3 布局待优化；真实模型 / QQ 未验；待负责人合并 PR #43 | docs/tasks/V4-9-companion-dates.md |
| V4-4a 统一主动调度 | 计划书 v4 功能四第一步：问候、心事、纪念日、追问都由一个调度器决定要不要开口；每天上限、两次间隔、没回不开新话题、追一句或算了、太久没回记小委屈；outreach_log（schema 12→13）；设置可调 | Codex 修复；Claude 审计 | 待 Claude 审计（PR #46 第五轮 9 项 P2 已修；QQ 主动投递需插件 0.10.2） | docs/tasks/V4-4a-scheduler.md |
| V4-4b 自己的生活 | 计划书 v4 功能四：按兴趣去网上看真实内容（有来源才算做过）、形成看法、值得说的经调度器分享、等回应后更新心情；activities 表（schema 13→14）；设置“自己的生活”可开关、调件数、看她最近在做什么 | Codex 修复；Claude 审计 | 待 Claude 复审（PR #48 已修复 ecf0791 的落单反引号流式卡顿） | docs/tasks/V4-4b-life.md |
| V4-6a 记忆召回基础与整份请求预算 | 计划书 v4 功能六的阶段二部分：存档旧事、更早聊天（FTS5 trigram 全文索引）、以前办过的事按关联召回；整份请求按用途预算、已知窗口硬上限拦截（目前只接入聊天生成链路，识图、后台分析、办事尚未接入）；schema 9→10 | Claude 实现；Codex 审查 | 待审查（与 V4-3 同在 PR #38） | 见 implementation_status 2026-11-11 |
| V4-3 持续情绪 | 计划书 v4 功能三：心情由事件推动、按半衰期回落；人格情绪倾向；来源复用同一轮 <mind/>；久别、办成事；提示词说明程度和原因；设置里看心情、手动哄好；schema 10→11 | Claude 实现；Codex 审查 | 已修复 Codex 第一轮 4 项，待复核（PR #38） | docs/tasks/V4-3-mood.md |
| V4-改名鲸鲸 | 默认角色改名“鲸鲸”（原“鲸娘”）：角色包名字、内置人格 v6、已有库一次性改名，QQ 认新旧两个名字 | Claude 实现；Codex 审查 | 待审查 | 负责人 2026-11-11 要求；叠在 V4-2 上 |
| V4-2 人格包 | 计划书 v4 功能二：人格新建 / 编辑 / 复制 / 切换 / 删除、版本回退、导入导出（兼容 SillyTavern 角色卡）、预览、内置人格升级不覆盖；schema 8→9 | Claude 实现；Codex 审查 | 已修复 Codex R1～R3，待 Codex 复核 | docs/tasks/V4-2-persona.md |
| V4-界面翻新 | 设置分组导航与概览引导、聊天面板精简、应用图标；计划书加入“界面与引导”和附件办事 | Claude 实现；Codex 审查 | 已合入（PR #33） | 负责人 2026-11-11 要求 |
| V4-MiniMax办事 | 办事接入 MiniMax（llm-pi-ai 路由，含 M3.1-Flash） | Claude 实现；Codex 审查及冲突修复 | 已合入（PR #32） | 负责人 2026-11-11 要求；处理 R3 已知问题 |
| V4-图库减负 | 表情图库：自己的图默认可用、含义自动识别、批量操作 | Claude 实现；Codex 审查 | 待审查 | 负责人 2026-11-11 实机反馈；见 implementation_status 同日记录 |
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
| PLAN-V4 | 项目计划书 v4：模型自由切换、人格包、持续情绪、自己的生活、真正退出、记忆召回与预算、圈子与跨天办事；开发参考成熟方案 | Claude 文档；Codex 审查；负责人确认 | 待审查（Claude，`task/project-plan-v4`） | docs/project_plan_v4.md；以后各功能的任务说明以此为依据 |
| V4-1 | 阶段一·地基：模型配置、凭据、按能力解析与快照、思考策略、模型设置页（里程碑：能正常聊天） | Claude 实现；Codex 审查；负责人实机验收 | 进行中（Claude，`task/v4-1-model-routing`，叠在 PLAN-V4 上） | docs/tasks/V4-1-model-routing.md；分 V4-1a / 1b / 1c 三个 PR |
| T14-M | 日常表情素材库（171 项：62 张 Twemoji PNG 随仓库，109 张鲸娘 GIF 由准备工具本机下载、不提交）；审计时指出的三项 P2 由 Claude 复核 | Codex 素材；Claude 修复及复测；Codex 审计 | 待复核（PR #14，已改为基于 collaboration-baseline；评测证据与审计报告按 #50 不再入库，结论写进 PR 描述与 implementation_status） | assets/stickers/daily-reactions-v2/README.md |
