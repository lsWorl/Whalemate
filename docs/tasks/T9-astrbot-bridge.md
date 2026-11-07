# T9：QQ 等聊天平台——AstrBot 插件转发给桌宠的大脑（P7，最后做）

负责：Claude 实现，Codex 方案与代码审查（2026-11-07 用户确认）　状态：第一版实现中（分支 `task/t9-astrbot-bridge`，用户 2026-11-07 决定提前做）

## 决定

不自己写 QQ / OneBot 适配器、白名单、限流、群管理。聊天平台交给 **AstrBot + NapCat**（AstrBot 负责平台接入，NapCat 把 QQ 接成 OneBot），
我们只写一个 **AstrBot 插件**，把选定会话的消息转给桌宠这边的大脑处理，再把回复发回去。
这样**人格和记忆只有一份**：你在 QQ 上和桌面上聊的是同一个她，QQ 里聊到的事她在桌面上也记得。

## 市场上已有的做法（调研，2026-11）

| 项目 | 做什么 | 和我们的关系 |
| --- | --- | --- |
| [AstrBot](https://docs.astrbot.app/) | 多平台聊天机器人 / Agent 框架（AGPL-3.0），QQ、企业微信、飞书、Telegram 等十几个平台；人格、MCP、插件上千个 | 用它接平台，不重复造 |
| [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin.html) | `filter.event_message_type` 收消息；`event.get_sender_id()` / `get_group_id()` / `unified_msg_origin` 拿会话；`event.stop_event()` 截停默认的大模型流程；`context.send_message(origin, chain)` 主动发消息 | 插件就用这几个接口 |
| [astrbot_plugin_dsh](https://github.com/CCYellowStar2/astrbot_plugin_dsh) | 把选定的聊天会话转给本机 DeepSeek Harness（另需 `dsh-astrbot-ingress`），支持追问、附件、显示过程 | 参考它的消息 / 追问 / 会话适配。但它默认保留 AstrBot 自己的人格，只在 `/dsh` 时进独立的 Harness 任务，不能直接装上就当作统一人格；也不能让它绕过桌宠直接启动另一套有工具权限的 DSH |
| [astrbot_plugin_self_open_llm_vtuber](https://github.com/AstrBotDevs/AstrBot/issues/6624)（"Alice 的桌面分身"） | AstrBot 插件 + 本机 WebSocket（12396）+ Live2D 桌面前端，语音输入 / 打断 / 表情联动；大脑在 AstrBot | 方向相反：它让 AstrBot 长出桌面形象；我们是桌宠长出 QQ。它的协议文档可参考消息格式 |
| AstrBot 的"OpenAI 兼容提供商"/ Agent 执行器（Dify、Coze、百炼、DeerFlow） | 把外部服务当模型或 Agent 接进 AstrBot | 不选（且未验证可行）：Agent 执行器不是任意外部地址的通用入口；OpenAI 兼容接口拿不到发送者、群号，没法做身份和场景隔离 |

## 要做（到时细化）

1. **身份先行（Codex 审查意见）**：桌面主人要在设置里**绑定**自己的平台稳定 ID（如 QQ 号）。只有绑定的那个人的私聊，才读写桌宠的私有上下文（`pet` 范围的记忆、聊天历史、摘要）。
   - 未绑定的人的私聊、所有群聊：只能用公开范围；历史、摘要、记忆写入都要按"平台 + 会话 + 发送者"隔离，不能读到桌面上的私聊内容。
   - 现在的 `ContextService` 历史和摘要只按 `character_id` 查，没有 principal / channel 隔离，这一步要先改（会动数据库，需要先在任务里约定表结构由谁改）。
2. 桌宠这边开一个本机接口（只监听 127.0.0.1，带令牌）：`POST /chat`，参数含平台、会话 id、发送者 id 和昵称、是否群聊、消息文本 / 图片；返回回复文本（去掉台词标注）和是否要回复。走现有的人格、按上面规则隔离的记忆、额度。**不在这个接口里开放干活（工具）**；以后要开，也只对绑定的主人、走同样的确认流程。
3. AstrBot 插件 `astrbot_plugin_aicompanion`：配置白名单会话、桌宠接口地址和令牌；命中白名单的消息转发、`stop_event()`，把回复发回。**桌宠不在线时默认回一句"她现在不在电脑旁"或不回复**，不回退到 AstrBot 自己的模型——那会变成第二个人格和另一套记忆。确实要回退的话，作为单独模式列出，不能称为同一个她。
4. 主动搭话：桌宠的"心事"在你不在电脑前时可以通过插件发到**已绑定的主人**的私聊（每天上限、安静时段沿用 P3 规则）。
5. 授权：AstrBot 是 AGPL-3.0，插件和桌宠之间只通过本机接口通信，不复制 AstrBot 代码。

B 站（看视频、评论）放到 QQ 做完后再评估；先查有没有现成的 AstrBot 插件可用。

## 第一版实现（2026-11-07）

再次调研后的取舍（尽量不造轮子）：
- 平台接入、群管理、指令、风控全部交给 AstrBot + NapCat；我们只写一个约 100 行的转发插件。
- AstrBot 的 Agent 执行器仍然只有内置、Dify、Coze、百炼、DeerFlow 五种，没有"任意 HTTP 地址"的通用入口；OpenAI 兼容提供商拿不到发送者和群号，做不了身份隔离。所以入站仍用插件。
- 主动消息（下一步）不再让插件轮询：AstrBot v4.18 起自带 OpenAPI，`POST /api/v1/im/message`（参数 `umo`、`message`，API Key 鉴权）可以直接向会话发消息，桌宠调用它即可。

| 部分 | 文件 | 做什么 |
| --- | --- | --- |
| 数据库 v4 | `storage/schema.py`、`storage/database.py` | `chat_messages` 加 `channel`（默认 `desktop`）；`recent_messages` / `add_message` / `finish_turn` 按频道读写。旧库自动迁移并先备份。**表结构这次由 Claude 改**（Codex 目前没有改库的任务） |
| 上下文 | `services/context_service.py`、`controllers/chat_controller.py` | `build(…, channel, scene, situation)`：私下场景（桌面、已绑定主人的私聊）照旧；公开场景不带摘要和信任度，只用公开范围记忆，加"不透露主人私事"的约束。`generate` 可关掉"动手"提议，用量记到 `qq` |
| 规则 | `bridge/service.py` | 主人私聊 → `desktop` 频道（同一段相处，记心情和记忆）；允许的群 → `qq:group:<群号>`；陌生人私聊默认不回，打开后 → `qq:user:<号>`；公开场合每天条数上限、额度用完时主人收到提示、公开场合静默 |
| 本机接口 | `bridge/server.py` | 127.0.0.1、Bearer 令牌（`hmac.compare_digest`）、`POST /v1/chat`、`GET /v1/ping`、请求体 ≤ 64 KB；一次只答一条 |
| 设置 | `ui/bridge_page.py`（设置 → 聊天平台） | 开关、端口、令牌（复制 / 更换）、主人 QQ 号、允许的群、陌生人、每日上限；保存即重启接口 |
| 插件 | `integrations/astrbot_plugin_aicompanion/` | 只转发 QQ（aiocqhttp / qq_official）里命中的会话；群里默认只在被 @ 或唤醒时；`/` 开头的指令留给 AstrBot；转发后 `stop_event()`；桌宠不在线时回离线提示或不回，**不回退到 AstrBot 的模型** |

**记忆和记录的边界（用户 2026-11-07 确认）**：
- 群聊记录在群内共享：同一个群的人共用一段记录，每条带"昵称#编号"（编号按账号全局分配、不重复，不暴露号码），`chat_messages.sender` 存完整稳定 ID（平台:号）。不做逐人隔离。
- **记忆全部共享**：桌面、主人私聊、群聊、陌生人私聊用同一份记忆。群里 / 陌生人那里听来的事由 `MemoryService.analyze(channel=…)`
  在后台整理（每个频道攒够 `bridge_memory_every` 条用户消息，默认 6），写清楚是谁的事、记成 `qq` 来源，桌面上也能用。
- 例外：主人在记忆页标成"只在私下用"（scope=`private`）的记忆，不在群聊和陌生人私聊里用。和主人的对话摘要、信任度只在私下用。
  公开来源的整理（群聊 / 陌生人）看不到 private 记忆；它只能新建或更新公开来源（qq / bili / public）的事实键，不能改桌面 / 私下记住的事实；
  和 private 记忆内容高度重合的新记忆不收（防止换个键复制）。这项检查在写入的同一个数据库写事务里按此刻的 private 状态做，
  所以整理进行中才被标成私下的也算；标成私下时，内容高度重合的已有副本一起标成私下。
- 身份在拿到处理锁之后、读上下文之前按最新设置重新判断；服务关闭 / 重启时排队中的旧请求作废。
- 原计划里"未绑定的人只能用公开范围、历史 / 摘要 / 记忆按平台 + 会话 + 发送者隔离"的要求，按用户这次的决定改为上面的规则。
- 并发：一条消息从核验额度到回复落库整段串行（进程级锁，重建服务实例也共用），当日公开条数用数据库事务原子预留；
  每一轮只看到自己那条消息为止的历史（`through_id`）。桌面和主人的 QQ 私聊共用 desktop 频道，两边同时说话时按先后进同一段记录、
  QQ 这一轮只回答到自己那条为止；桌面这一侧的流程不变（主人本人同时在桌面和 QQ 上说话的情况很少，先不为它加锁等待）。

还没做：主动消息（AstrBot OpenAPI）、图片 / 语音消息（目前只转文字）、真机 QQ 走通（需要用户装 AstrBot 和 NapCat）。


## 第二步：一键接入 QQ（分支 `task/t9-qq-oneclick`，2026-11-07 起）

目标：用户只装桌宠，在"设置 → 聊天平台"点"一键接入 QQ"，桌宠按需下载并配置 NapCat 和 AstrBot、启动它们，用户只需扫码登录、确认主人账号、勾选允许的群。

### 上游核实（2026-11-07）

| 组件 | 选用 | 依据 |
| --- | --- | --- |
| NapCat | `NapCat.Shell.Windows.Node.zip` v4.18.41（118 MB，SHA-256 `51fbb10e…27d3f0`），GitHub 官方发布页按需下载 | 官方 release 流水线把 NapCat.Shell、QQ 9.9.31 运行所需的 wrapper.node 等文件和 node.exe 打成一个自包含包（`release-publish.yml` 的 assemble-windows-node），入口 `napcat.bat` → `node.exe index.js`；**不需要预装 QQ、不注入系统 QQ、不要管理员权限**。`NapCat.Shell.Windows.OneKey.zip` 只是一个再去下载的安装器，不可控，不选 |
| NapCat 程序接口 | WebUI 后端（express）：`POST /api/auth/login`（body `{hash: sha256(token + ".napcat")}` → `data.Credential`，之后 `Authorization: Bearer <Credential>`）、`POST /api/QQLogin/CheckLoginStatus`（`isLogin`、`isOffline`、`qrcodeurl`、`loginError`）、`POST /api/QQLogin/GetQQLoginQrcode`、`POST /api/QQLogin/RefreshQRcode`、`POST /api/QQLogin/GetQQLoginInfo` | 源码 `packages/napcat-webui-backend/src/router/QQLogin.ts`、`api/Auth.ts`、`helper/SignToken.ts` |
| NapCat 配置 | 环境变量 `NAPCAT_WORKDIR`（config / logs / cache 的位置）、`NAPCAT_QQ_DATA_DIR`（QQ 数据目录，和用户自己的 QQ 分开）；`config/webui.json`（host 默认 `::` 即所有网卡，**我们写 127.0.0.1**；port 6099；token）；`config/onebot11.json` 是首次登录时复制成 `onebot11_<QQ号>.json` 的模板（`network.websocketClients[]`：url、token、reconnectInterval、heartInterval…）；扫码图片保存到 `cache/qrcode.png`；`-q <QQ号>` 快速登录 | `napcat-common/src/path.ts`、`napcat-core/helper/config-base.ts`、`napcat-onebot/config/config.ts`、`napcat-shell/base.ts`。不用 `NAPCAT_WEBUI_SECRET_KEY`：NapCat 会把它明文写进日志 |
| AstrBot | PyPI `AstrBot==4.29.3`（wheel SHA-256 `b78d8ab2…be6be8e`，管理面板已打包在 wheel 里），用 uv 0.13.5（`uv-x86_64-pc-windows-msvc.zip`，SHA-256 `a38b8d19…fd1019a`）在桌宠自己的目录里装 Python 3.12 和独立虚拟环境 | 官方"脚本部署"（`install.ps1`）也是 uv + Python 3.12 + `astrbot`，但它会改用户的 PATH（`uv tool update-shell`），所以我们照它的做法、只放进自己的目录。`ASTRBOT_ROOT` 指定数据目录；`astrbot run`；`.astrbot` 标记文件；`data/cmd_config.json` 缺的键启动时自动补默认值；`ASTRBOT_DASHBOARD_INITIAL_PASSWORD` 首次启动时设置面板密码 |
| AstrBot ↔ NapCat | AstrBot 的 `aiocqhttp`（OneBot v11 反向 WebSocket）平台：`ws_reverse_host` 写 127.0.0.1，`ws_reverse_port` 自动挑空闲端口，`ws_reverse_token` 随机；NapCat 的 websocketClients 指向 `ws://127.0.0.1:<端口>/ws`，token 相同 | `astrbot/core/config/default.py`、`aiocqhttp_platform_adapter.py` |
| 是否连上 | 插件每 15 秒向桌宠发心跳：经 `context.get_platform('aiocqhttp').get_client()` 调 OneBot `get_login_info` / `get_group_list`，成功 = NapCat 已连上 AstrBot；心跳顺便带回群列表，用户直接在桌宠里勾选允许的群 | AstrBot 平台状态只表示"服务起来了"，不表示 NapCat 连上 |
| NapCatQQ Desktop | 不集成 | 它是 GPL-3.0 的 Tauri 桌面控制台，自己能装 NapCat、对接 AstrBot，但没有给其他程序调用的安装 / 启动接口（MCP 默认关闭且只读）。已经用它管理 NapCat + AstrBot 的用户走"接入已有实例" |

许可：
- NapCat 是 "Limited Redistribution License"：禁止商用，再分发须带全文许可；README 另写明禁止未经授权基于 NapCat 代码开发。
- 所以**不打包、不修改、不再分发** NapCat。由用户的电脑从 NapCat 官方 GitHub 发布页下载原版文件，界面上显示来源和许可提示，并要求用户确认。
- AstrBot 是 AGPL-3.0，同样按需从 PyPI 安装，不复制代码；我们的插件只通过 AstrBot 的插件接口工作。

### 方案

两种方式（设置页二选一）：

1. **自动安装并管理**（默认）。
   - 组件放在 `%LOCALAPPDATA%\AICompanion\qq\`，不放项目目录，不碰桌宠的 `.venv`、用户的 QQ 和 PATH。
   - 步骤：下载 → 校验 SHA-256 → 解压到临时目录 → 校验关键文件 → 原子切换到"当前版本"；失败时旧版本保持可用。每一步可以重试，已校验的下载会复用。
   - 安装记录 `manifest.json` 写版本、来源 URL、SHA-256、`uv pip freeze` 的结果，以及我们创建的文件和进程；只清理和结束我们自己记录的东西。
   - 自动生成以下配置：
     - NapCat：`webui.json`、`onebot11.json`，以及已登录账号的 `onebot11_<号>.json`（只改我们那一条 websocketClient）；
     - AstrBot：`cmd_config.json` 里我们的那个 aiocqhttp 平台（按 id `aicompanion_qq` 识别）和面板地址；
     - 插件目录和插件配置。
   - 端口自动避开已占用的；所有监听地址都是 127.0.0.1。
   - 两段连接各用独立的随机令牌，不写日志：NapCat ↔ AstrBot 一个，插件 ↔ 桌宠一个；另外 NapCat WebUI 令牌和 AstrBot 面板密码也各自随机。
   - 进程以独立后台进程运行（无窗口），输出写到我们目录下的日志；桌宠退出时按设置停止或保留。
2. **接入已有实例**。用户选 AstrBot 的根目录（含 `data/`）。只安装 / 更新我们的插件目录（目录名和 `metadata.yaml` 的 name 都是我们的才覆盖），只写我们插件的配置文件；不改 `cmd_config.json`、其他插件和 NapCat、账号数据。NapCat ↔ AstrBot 的连接由用户原来的方式负责，界面按心跳显示状态。

状态与诊断（从前往后，第一个不满足的就是当前状态，配一句下一步该做什么）：
组件未准备 → 未启动 → NapCat 未登录（显示二维码，等扫码）→ OneBot 未连接（NapCat 没连上 AstrBot）→ 插件未连接（AstrBot 没跑 / 插件没加载 / 令牌不对）→ 已连接。
桌宠离线时插件回离线提示，不回退到 AstrBot 的模型；人格、聊天记录、记忆共享 / private 规则沿用第一版。

### 需要改动的公共文件 / 新增依赖

- 公共文件：`ui/settings_window.py` 只改聊天平台这一页的挂载（已有）。`app.py` 加启动时自动拉起组件和退出时停止组件，几行，是 T9 自己加过的位置。**不改** `main_window.py`、`database.py`、`config.py`、`pyproject.toml`。
- 新增 Python 依赖：无。下载用标准库 `urllib`，解压用 `zipfile`，二维码直接显示 NapCat 生成的 `qrcode.png`。
- 新增外部组件（运行时按需下载，不进安装包，不进仓库）：NapCat.Shell.Windows.Node v4.18.41、uv 0.13.5、Python 3.12（由 uv 下载 python-build-standalone，uv 自带校验）、AstrBot 4.29.3 及其依赖（uv 按 PyPI 公布的哈希校验，装完记录 freeze）。
