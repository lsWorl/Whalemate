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

**第一版的隔离边界（Codex 审计后明确，待用户确认）**：
- 群聊是"群共享的公开上下文"：同一个群的人共用一段记录，每条带"昵称#号码后四位"，数据库 `chat_messages.sender` 存完整稳定 ID（平台:号）；
  不同群、陌生人私聊、桌面之间互相不可见。原计划的"平台 + 会话 + 发送者"三级隔离对群聊不合适（她会看不懂群里在聊什么），第一版不做逐人隔离。
- 并发：一条消息从核验额度到回复落库整段串行（进程级锁，重建服务实例也共用），当日公开条数用数据库事务原子预留；
  每一轮只看到自己那条消息为止的历史（`through_id`）。桌面和主人的 QQ 私聊共用 desktop 频道，两边同时说话时按先后进同一段记录、
  QQ 这一轮只回答到自己那条为止；桌面这一侧的流程不变（主人本人同时在桌面和 QQ 上说话的情况很少，先不为它加锁等待）。

还没做：主动消息（AstrBot OpenAPI）、图片 / 语音消息（目前只转文字）、真机 QQ 走通（需要用户装 AstrBot 和 NapCat）。
