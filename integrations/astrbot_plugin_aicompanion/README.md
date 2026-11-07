# astrbot_plugin_aicompanion：让 QQ 上的她和桌面上是同一个她

QQ 接入不自己写：**NapCat** 把 QQ 接成 OneBot，**AstrBot** 负责平台、群管理和指令，这个插件只做一件事——
把选定的 QQ 会话转给本机 AICompanion 桌宠，由桌宠的大脑（同一个人格、同一份记忆）回复。

```
QQ ⇄ NapCat ⇄ AstrBot ─(本插件，POST 127.0.0.1)→ AICompanion 桌宠 → DeepSeek
```

## 谁能和她说话（由桌宠判断）

| 会话 | 怎么处理 |
| --- | --- |
| 你（桌宠里绑定的主人 QQ 号）的私聊 | 和桌面同一段相处：共用聊天记录、记忆、心情 |
| 允许的群（插件和桌宠两边都要填） | 公开场合：被 @ 或叫唤醒词时回复；只用"公开"范围的记忆，每个群一段独立记录 |
| 陌生人私聊 | 默认不回；桌宠设置里打开后按公开场合回复 |
| 其他群 | 插件不管，交还给 AstrBot |

聊天平台上她不会动手干活。桌宠没开或接口没打开时，插件回一句离线提示（可留空 = 不回），**不会改用 AstrBot 自己的模型**。

## 安装步骤（Windows，同一台电脑）

1. **桌宠**：设置 → 聊天平台，勾选"打开本机接口"，填你的 QQ 号和允许的群号，保存；点"复制令牌"。
2. **AstrBot**：按官方文档安装并打开管理面板（默认 http://localhost:6185）：https://docs.astrbot.app/deploy/astrbot/desktop.html（桌面版）或 https://docs.astrbot.app/deploy/astrbot/windows.html
3. **NapCat**：按 AstrBot 文档接入 QQ（OneBot v11 反向 WebSocket）：https://docs.astrbot.app/deploy/platform/aiocqhttp/napcat.html
   建议用一个小号当机器人，不要用主号。
4. **装插件**：把本文件夹整个复制到 AstrBot 的 `data/plugins/astrbot_plugin_aicompanion/`，在管理面板"插件"里重载；
   插件配置里填：桌宠接口地址（默认 `http://127.0.0.1:18765`）、令牌、交给桌宠的群号。
5. 用主号私聊机器人说一句话，桌宠应该以她的口吻回复；在桌宠的聊天面板里也能看到这段对话。

## 说明

- 插件和桌宠只通过本机 HTTP 接口通信，不复制 AstrBot 代码；插件按 AstrBot 插件接口（AGPL-3.0）编写，随 AstrBot 使用时遵循其许可。
- 依赖：`aiohttp`（AstrBot 自带）。
