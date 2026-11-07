# T9：QQ 等聊天平台——AstrBot 插件转发给桌宠的大脑（P7，最后做）

负责：待定　状态：最后再做（P8 之后）

## 决定

不自己写 QQ / OneBot 适配器、白名单、限流、群管理。聊天平台交给 **AstrBot + NapCat**（AstrBot 负责平台接入，NapCat 把 QQ 接成 OneBot），
我们只写一个 **AstrBot 插件**，把选定会话的消息转给桌宠这边的大脑处理，再把回复发回去。
这样**人格和记忆只有一份**：你在 QQ 上和桌面上聊的是同一个她，QQ 里聊到的事她在桌面上也记得。

## 市场上已有的做法（调研，2026-11）

| 项目 | 做什么 | 和我们的关系 |
| --- | --- | --- |
| [AstrBot](https://docs.astrbot.app/) | 多平台聊天机器人 / Agent 框架（AGPL-3.0），QQ、企业微信、飞书、Telegram 等十几个平台；人格、MCP、插件上千个 | 用它接平台，不重复造 |
| [AstrBot 插件开发](https://docs.astrbot.app/dev/star/plugin.html) | `filter.event_message_type` 收消息；`event.get_sender_id()` / `get_group_id()` / `unified_msg_origin` 拿会话；`event.stop_event()` 截停默认的大模型流程；`context.send_message(origin, chain)` 主动发消息 | 插件就用这几个接口 |
| [astrbot_plugin_dsh](https://github.com/CCYellowStar2/astrbot_plugin_dsh) | 把选定的聊天会话转给本机 DeepSeek Harness（另需 `dsh-astrbot-ingress`），支持追问、附件、显示过程 | "插件转发给本机服务"的同类做法，可参考它的会话和追问处理 |
| [astrbot_plugin_self_open_llm_vtuber](https://github.com/AstrBotDevs/AstrBot/issues/6624)（"Alice 的桌面分身"） | AstrBot 插件 + 本机 WebSocket（12396）+ Live2D 桌面前端，语音输入 / 打断 / 表情联动；大脑在 AstrBot | 方向相反：它让 AstrBot 长出桌面形象；我们是桌宠长出 QQ。它的协议文档可参考消息格式 |
| AstrBot 的"OpenAI 兼容提供商"/ Agent 执行器（Dify、Coze、百炼、DeerFlow） | 把外部服务当模型或 Agent 接进 AstrBot | 备选：把桌宠的大脑包装成 OpenAI 兼容接口注册进去，零插件代码；但拿不到发送者、群号，没法区分私聊 / 群聊的记忆范围，所以不选 |

## 要做（到时细化）

1. 桌宠这边开一个本机接口（只监听 127.0.0.1，带令牌）：`POST /chat`，参数含平台、会话 id、发送者 id 和昵称、是否群聊、消息文本 / 图片；返回回复文本（去掉台词标注）和是否要回复。走现有的人格、记忆（群聊用 P3 已有的 `scope` 公开范围，私聊用 `pet`）、额度。
2. AstrBot 插件 `astrbot_plugin_aicompanion`：配置白名单会话、桌宠接口地址和令牌；命中白名单的消息转发、`stop_event()`，把回复发回；桌宠不在线时按配置回退到 AstrBot 自己的模型或不回复。
3. 主动搭话：桌宠的"心事"在你不在电脑前时可以通过插件发到指定私聊（每天上限、安静时段沿用 P3 规则）。
4. 授权：AstrBot 是 AGPL-3.0，插件和桌宠之间只通过本机接口通信，不复制 AstrBot 代码。

B 站（看视频、评论）放到 QQ 做完后再评估；先查有没有现成的 AstrBot 插件可用。
