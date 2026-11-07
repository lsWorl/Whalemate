# T12：QQ 识图和表情包

负责：Codex；状态：实现及真实模型验证完成，待 PR 复审 / 用户 QQ 真机验收。用户已确认首版只覆盖 QQ 私聊和允许的群。

## 方案及调研

- DeepSeek 最新官方视觉接口接受标准 `image_url` 内容块，模型 `deepseek-flash`。复用现有兼容接口客户端的 `chat()`：用同一官方 DeepSeek 凭据识别图片，再让原聊天模型依据描述回复；人格、场景权限、历史和记忆仍只有一份。不强制改变用户的聊天模型。
- AstrBot 4.29.3 已有图片解析、临时文件和 `Image.fromBase64()` 消息链。插件复用这些接口传输，桌宠不根据模型提供的 URL 抓图、不开放任意本机文件读取。
- 参考社区 meme_manager 的“分类图库 + 主模型选标签”，支持其开放基础目录 `manifest.json` + `memes/<分类>/<图片>`，也支持普通分类目录；不重复实现图库 WebUI、云同步、向量库或第二套人格。首版附带选用仓库已有鲸娘姿态的默认图库，不复制第三方表情图。
- 无新增依赖：桌宠复用 PySide6 图像解码与现有标准库客户端；AstrBot 自带 Pillow 用于压缩上传的图片。新增配置存在既有设置表，不改数据库表结构、`main_window.py`、`config.py` 或 `pyproject.toml`。

依据（2026-11-07 核对）：
- https://api-docs.deepseek.com/guides/vision/
- https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/message/components.py （同时核对已固定的 4.29.3 wheel）
- https://github.com/anka-afk/astrbot_plugin_meme_manager
- https://github.com/anka-afk/astrbot-meme-pack-index/blob/main/ASTRBOT_MEME_PACK_PROTOCOL.md

## 接口和边界

- 输入图片仅在当前请求内传输，不落入聊天数据库；只保存识别出的描述。先核验聊天场景和额度，再识图。未获准的私聊 / 群不识图；群旁听不自动识图。
- 限制图片数量、字节数和像素；插件先压缩，桌宠再次验证；不把 base64、签名 URL 或图片路径写入提示、日志或诊断。
- 表情包由同一个回复模型选择本地图库里的分类，一次至多一张、带冷却；模型不能指定文件路径。图库只读取用户指定的图片目录和仓库自带原创图。
- QQ 权限、private 记忆和用量规则沿用 T9。与 Claude 的 PR #9 共享 `bridge/service.py`、插件 `main.py` 和 `ui/bridge_page.py`，采用独立辅助模块减少冲突；合入时需保留旁听 / 插话及 PR #8 的 BOM / 诊断修复。

## 验证

离线单测涵盖图文 / 纯图片、无权请求不调用视觉模型、额度、超大 / 非图片拒绝、只传描述、标签白名单 / 路径限制、表情图片消息和失败降级、设置保存；每个 PR 跑完整 `tools/run_checks.bat`。
真实模型和 QQ 收发另行记录，不将桩测试视为真机验收。

## 已验证结果

- 16 项媒体单测：权限 / 额度、图文和纯图片、非法 / 超大输入、只保存描述、失败降级、社区图库、白名单 / 冷却、传输标签清理、真实本机 HTTP 大图片往返。
- 固定 AstrBot 4.29.3 的真实 Python API：加载插件、解析受管理图片、转换 JPEG、构造 Plain + Image 消息链全部通过，不连接 QQ。
- 用户明确授权本次仅从 `.env` 提取 DeepSeek API 字段并调用官方模型；使用临时数据库和合成蓝色方块 + 数字 4827，视觉与原大脑均识别正确，并返回 1 张开心表情。两次真实模型调用 3.91 秒，prompt 1105 / completion 350 tokens；历史中无 base64 / 发送标签。测试脚本调用后自动删除，原生 AstrBot 临时验证脚本也已删除，只留无凭据的结果摘要。
- 设置页已用合成数据按真实主题渲染并检查布局；`pet_smoke_check` 新增媒体设置检查。
- 尚未验证真实 QQ 网络收发、群内实际体验、长期运行或其他供应商。识图只复用官方 DeepSeek 凭据；其他供应商不会被自动上传图片。

真机验收步骤见 `docs/qq-media-validation.md`。PR #9 尚未合入时，本分支仍基于包含 PR #8 的协作基线；合入 PR #9 后需更新基线、保留其旁听 / 插话 / 权限修复并处理共享文件冲突。
