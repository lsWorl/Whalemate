# AICompanion 模块说明

这份文档用于 review 当前代码结构。项目采用分层设计：UI 负责显示，Controller 负责流程，Service 负责领域服务，Storage 负责 SQLite，LLM 层负责模型调用。

## 启动入口

### `main.py`

根目录启动文件。它只导入 `aicompanion.app.main()` 并运行，方便你直接执行：

```powershell
.\.venv\Scripts\python.exe main.py
```

### `src/aicompanion/app.py`

应用装配入口，负责创建这些对象：

- `QApplication`
- `Database`
- `MemoryService`
- `LLMClient`
- `ChatController`
- `MainWindow`
- `ProactiveMessageService`

这里不写具体业务逻辑，只负责把模块接起来。

## 配置模块

### `src/aicompanion/config.py`

读取 `.env` 和系统环境变量，并生成 `Settings`。

目前支持：

- `APP_NAME`
- `DATABASE_PATH`
- `LLM_PROVIDER`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_TEMPERATURE`
- `LLM_TIMEOUT_SECONDS`

默认 `LLM_PROVIDER=mock`，不需要 API key。

## UI 模块

### `src/aicompanion/ui/main_window.py`

主窗口，负责：

- 显示聊天记录
- 收集用户输入
- 点击发送或回车发送消息
- 创建后台线程请求模型
- 接收流式 token 并追加显示

它不直接读写数据库，也不直接调用 API。

### `src/aicompanion/ui/chat_worker.py`

后台工作对象，运行在 `QThread` 中。

它调用 `ChatController.stream_user_message()`，并通过 PySide6 Signal 把结果发回 UI：

- `token_received`：收到一段模型输出
- `finished`：回复结束
- `failed`：请求失败

这个模块的作用是避免 API 请求阻塞窗口。

## Controller 模块

### `src/aicompanion/controllers/chat_controller.py`

聊天流程核心，负责：

- 保存用户消息
- 更新情绪状态
- 提取长期记忆
- 读取最近聊天历史
- 构建系统提示词
- 调用 LLM
- 保存 AI 回复
- 判断是否需要主动消息

UI 层只调用它，不关心这些细节。

## LLM 模块

### `src/aicompanion/llm/base.py`

定义统一接口：

- `ChatTurn`
- `LLMClient`

只要实现 `generate_reply()` 和 `generate_reply_stream()`，就可以被项目使用。

### `src/aicompanion/llm/mock_client.py`

本地模拟模型，不访问网络。

用于：

- 没有 API key 时开发
- 测试 UI 流式显示
- 验证数据库保存流程

### `src/aicompanion/llm/openai_compatible_client.py`

真实 API 客户端，调用 OpenAI-compatible Chat Completions 接口：

```text
POST {LLM_BASE_URL}/chat/completions
```

它支持：

- 非流式完整回复
- SSE 流式回复
- HTTP 错误处理
- 网络错误处理

当前使用 Python 标准库 `urllib`，没有额外引入 API SDK。

### `src/aicompanion/llm/factory.py`

根据 `LLM_PROVIDER` 创建具体客户端。

当前支持：

- `mock`
- `openai_compatible`

以后添加新模型供应商时，优先在这里扩展。

## Storage 模块

### `src/aicompanion/storage/schema.py`

SQLite 表结构。

当前包含：

- `chat_messages`：聊天记录
- `memories`：长期记忆
- `reminders`：任务提醒，后续实现
- `emotion_state`：角色情绪状态

### `src/aicompanion/storage/database.py`

SQLite 数据访问层。

负责：

- 初始化表
- 保存和读取聊天记录
- 保存和读取长期记忆
- 保存和读取情绪状态

其他模块不直接写 SQL，统一通过这里访问数据库。

## Service 模块

### `src/aicompanion/services/memory_service.py`

长期记忆服务。

当前是关键词规则：

- `我喜欢`
- `我讨厌`
- `记住`
- `我的名字`
- `我正在`

命中后保存为长期记忆。后续可以升级成 LLM 自动总结。

### `src/aicompanion/services/proactive_service.py`

主动消息定时服务。

它每 60 秒触发一次检查，真正的主动消息规则在 `ChatController` 中。

## Domain 模块

### `src/aicompanion/domain/character.py`

角色档案。

目前定义默认角色的：

- 名字
- 性格描述
- 说话风格

后续可以加入 Live2D 模型路径、表情映射、角色口癖等配置。

### `src/aicompanion/domain/emotion.py`

情绪状态。

目前包含：

- `mood`
- `affection`
- `loneliness`
- `energy`

后续可以把这些状态映射到：

- 回复语气
- 主动消息频率
- Live2D 表情
- TTS 语音参数

## 当前消息流程

用户发送消息后的完整流程：

```text
MainWindow
  -> ChatWorker(QThread)
  -> ChatController.stream_user_message()
  -> Database 保存 user 消息
  -> EmotionState 更新情绪
  -> MemoryService 尝试保存长期记忆
  -> Database 读取最近聊天记录
  -> ChatController 构建 system prompt
  -> LLMClient.generate_reply_stream()
  -> ChatWorker 发出 token_received
  -> MainWindow 逐段更新 AI 消息
  -> Database 保存完整 assistant 回复
```

## 如何切换到真实 API

1. 复制 `.env.example` 为 `.env`。
2. 修改：

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=你的 API key
LLM_MODEL=gpt-4o-mini
```

3. 运行：

```powershell
.\.venv\Scripts\python.exe main.py
```
