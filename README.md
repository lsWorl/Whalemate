# AICompanion

Windows desktop AI companion prototype built with Python, PySide6, SQLite, and a pluggable LLM service.

## Current milestone

The desktop experience now includes a bundled Haru Live2D sample, sentence-based
speech output, microphone/VAD input, role-isolated storage, burst-message merging,
interruption, memory settings, reminders, and tray lifecycle.

Qwen speech now supports an original anime-inspired voice, per-character tone and
style settings, in-app preview, and bounded next-sentence prefetch. DeepSeek chat
and Qwen speech use separate credentials. This is an original designed voice,
not a reproduction of an actor's voice. Audio samples are in `docs/audio`.

Japanese dubbing can translate Chinese dialogue while preserving Chinese chat
history and subtitles. An Emilia fan-persona preset and the approved generated
illustration preview are available; the illustration has not been rigged into a
Cubism model. The local experimental voice is separate from public release assets.

See [implementation status](docs/implementation_status.md) for verified behavior,
known limitations, speech configuration, and Windows packaging. Speaker echo
cancellation and the Android client are not implemented; use headphones for calls.

- PySide6 chat window
- Layered app structure
- SQLite schema for chat messages, memories, reminders, and emotion state
- Local mock LLM service for offline development
- OpenAI-compatible LLM API client
- Streaming reply display through a background QThread
- Basic proactive message scheduler

## Run

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe main.py
```

鲸娘桌宠现支持用户指定的 Aboutme 大肥鱼完整帧动画。第三方素材需本机安装：

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r tools\aboutme-requirements.txt
.\.venv\Scripts\python.exe tools\install_aboutme_pet.py
~~~

未安装时显示基线立绘；应用启动不会联网下载。来源、使用范围与离线重装见 [Aboutme 接入说明](docs/aboutme-pet.md)。

By default, the app uses `LLM_PROVIDER=mock`, so it can run without an API key.

“干活”功能的可复现安装（DeepSeek Harness 与官方 MCP SDK）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-agent-lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[agent]"
```

MCP 服务仅在干活或协议自检时启动，使用本机回环 HTTP 和进程级随机令牌。
基础聊天与桌宠不要求安装 Agent 可选依赖。`tools/setup_dsh.bat` 只安装锁定依赖并运行
`pip check`，不自动调用模型。真实验收另运行 `tools/run_agent_checks.bat`：只使用进程环境中的
`LLM_API_KEY` / `DEEPSEEK_API_KEY` 及对应接口地址，不加载 `.env`；缺少凭据时退出 2 并记录未执行。
验收的文件、假密钥、回收区及 DSH 会话均位于新建临时目录，不访问博客或真实用户文件。
工作区在项目所在盘的 `.aicompanion-live` 下新建（Windows 例如 `D:\.aicompanion-live\fixture-*`），
避开仓库及上层的 AGENTS.md / CLAUDE.md；DSH 会话单独放在 LocalLow 临时目录。结束后只清理本次新建的子目录。

To use a real model API, copy `.env.example` to `.env` and set:

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_api_key
LLM_MODEL=gpt-4o-mini
```

Many providers support OpenAI-compatible `/chat/completions` APIs. In that case,
replace `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL` with that provider's values.

## LLM Diagnostics

Before opening the desktop UI, you can test the configured provider with:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics
```

To send a custom message:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics "你好，请简单介绍一下你自己"
```

To test a non-streaming response:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics --no-stream
```

## Project Layout

```text
src/aicompanion/
  app.py                  # QApplication bootstrap
  config.py               # environment/config loading
  controllers/            # app workflow orchestration
  domain/                 # character, emotion, prompt models
  llm/                    # LLM provider abstraction
  services/               # memory, reminders, proactive messages
  storage/                # SQLite access and schema
  ui/                     # PySide6 widgets
```

For a module-by-module explanation, see [docs/module_guide.md](docs/module_guide.md).

## Voice Calls

Recognition and synthesis are configured independently. For Qwen recognition, set
`ASR_PROVIDER=qwen`, `QWEN_ASR_MODEL=qwen-audio-3.0-asr-flash` and an independent
`QWEN_API_KEY`. The voice settings page can switch between Qwen and Zhipu.
The Qwen key is never replaced by the chat provider's key.

Calls recognize complete VAD-separated utterances, not live partial transcripts.
Speaking interrupts playback; the reply waits for ongoing speech and pending
recognition. Three consecutive recognition errors pause the call. Use headphones:
speaker echo cancellation and physical-device stress testing are not yet complete.

`tools/verify_asr.py` checks synthetic Chinese/Japanese audio with the real API
(paid on first run or `--refresh`). `tools/smoke_call.py` tests the Qt call workflow
with a virtual microphone and temporary database, without network or recording.

## Desktop Pet v2 (DeepSeek whale)

The app now starts **directly as a desktop character** (DeepSeek-inspired fan
"鲸娘" by default). The chat panel is hidden until opened from the pet's
right-click menu or by double-clicking the tray icon. Click to poke, drag to pick
her up, double-click for a quick input box. Only the character's own pixels catch
the mouse; transparent areas pass clicks through. She stays on top, hides while a
fullscreen app is in front, and can start with Windows (right-click → 桌宠设置).

Characters are packs under `src/aicompanion/characters/` (persona, lines, model
binding, sprites). Rebuild the whale sprites after updating the art pack:

```powershell
.\.venv\Scripts\python.exe -m pip install pillow numpy scipy
.\.venv\Scripts\python.exe tools\build_character_pack.py output\desktop-pet\asset-pack-v3 src\aicompanion\characters\deepseek-whale\sprites
```

Optional dedicated model key: `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL`. Without it the
whale uses the default `LLM_*` settings. The bundled Haru sample has been removed;
imported Cubism models are still supported. Status: see `docs/implementation_status.md`.

## Desktop Pet (v1 notes)

The desktop window now shows **only the character**, with a transparent alpha
background, no caption, toolbar, subtitle box, status panel or chat frame.
Drag the character to move it. Right-click to resize, change topmost state,
choose an action, start/end calls, interrupt, open the control panel or quit.
Position and size are saved locally. Calls never start the microphone automatically.

The approved Emilia illustration now has an explicitly separate **2D mesh effect**:
breathing, blinking, subtle head/hair movement, lip sync, blush and six gentle
actions. This is a Pixi mesh renderer, **not a Cubism `.moc3` model**. Generated
eye/mouth textures are sampled only in local face masks. Asset provenance and
prompts are recorded in `assets/live2d/Emilia/animation-source.md`.

Emilia is an optional character: switch to her from the pet's right-click menu
(切换角色). Standard Cubism models
remain supported separately; Haru is only a bundled test sample.

To replace Haru, extract an obtained Cubism 3/4 runtime package and select its
`.model3.json` from the pet menu. Keep all referenced `.moc3`, PNG, motion and
expression files together. Structural validation rejects missing resources,
external paths and unsupported model versions; actual animation still requires
successful Core rendering. See [model sources](docs/live2d_model_sources.md).

Offline verification: `tools/smoke_pet.py --emilia`; packaged verification:
`AICompanion.exe --emilia-smoke-test`. These use temporary data without cloud calls.
`--pet-smoke-test` still verifies the separate genuine Cubism path.
