<div align="center">

<img src="src/aicompanion/assets/icons/aicompanion-256.png" width="128" alt="Whalemate icon">

# Whalemate · 鲸伴

**A whale girl who lives on your Windows desktop. She moves, remembers, talks, and gets things done for you.**

[简体中文](README.md) | English

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6?logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/status-in%20development-orange)

[Features](#-features) · [Quick start](#-quick-start) · [Configuration](#️-configuring-models-and-keys) · [Roadmap](#️-roadmap) · [Contributing](#-contributing) · [Acknowledgements](#-acknowledgements) · [License](#-license-and-notices)

</div>

> [!NOTE]
> The Chinese README is the primary version. The project's UI, docs and code comments are in Chinese;
> if this page and the Chinese one disagree, the Chinese one wins.

---

Whalemate (鲸伴, "whale companion") is an AI companion that lives on your Windows desktop. Her default character is
"Dafeiyu" (大肥鱼, "big fat fish"), a fan-made girl based on the DeepSeek whale. She moves around your desktop, remembers
what you've talked about, answers in her own voice, gets a little moody, and actually does the tasks you hand her.

Unlike a typical chat client, Whalemate cares about *who she is*: **switch the model and she is still herself.**
Persona, memory, relationships and channels are kept separate, so you can swap model providers, shape her personality,
and talk to the same her on the desktop and on QQ without losing your shared history.

> [!NOTE]
> Whalemate is under active development and some features are unfinished. The lists below mark what works today and
> what is still on the roadmap. The real status of each feature is tracked in
> [docs/implementation_status.md](docs/implementation_status.md) (Chinese).

<div align="center">
<img src="art/deepseek-whale/approved-poses-preview.png" width="720" alt="Dafeiyu's ten poses">
</div>

## ✨ Features

### 🐋 Desktop character
- A transparent, frameless desktop pet. Only her own pixels catch the mouse: click to poke, drag to pick her up, double-click for quick input.
- Layered mesh-and-bone animation with ten states (idle, talking, thinking, working, sleepy, celebrating, …). She steps aside when a fullscreen app is in front.
- Characters are organised as *character packs* (persona, lines, model binding, art). Standard Live2D Cubism 3/4 models can also be imported.

### 💬 Chat and expression
- Streaming replies. Several quick messages are merged into one turn, and you can interrupt at any time.
- Stickers, motions and mood parameters come with each reply. Model reasoning never appears in bubbles, speech or logs.
- Without a configured model she runs on an offline mock, so you can try the UI without an API key.

### 🔊 Voice and calls
- Replies are spoken by default. "Smart reading" picks out the important parts of long replies and skips code, paths, tables and lists.
- Speech synthesis via Breeze Blue, Qwen, Zhipu or Windows' built-in voices. If cloud synthesis fails she falls back to local speech and still finishes the reply.
- Voice calls: WebRTC VAD segmentation, cloud (Qwen, Zhipu) or local SenseVoice recognition. Start speaking and she stops to listen.
- Japanese dubbing: chat and subtitles stay in Chinese, only the spoken sentences are translated.

### 🧠 Memory and relationships
- Decaying memory cards with an archive and sources. Anything you explicitly delete won't sneak back in reworded.
- Older chats and past tasks are recalled with SQLite FTS5, and every request stays within a context budget.
- Mood is driven by events and fades over time: ignore her and she sulks, comfort her and she calms down. Affection is long-term, so one argument doesn't reset it.
- Persona packs can be created, edited, switched, rolled back, imported and exported (compatible with common SillyTavern character-card fields). Switching persona keeps her memories.

### 🛠️ Getting things done
- Tasks are planned by [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) and executed by the pet's own tools; sensitive actions ask you first.
- Tools are exposed over MCP on the local loopback only, with a fresh random token per run.
- Works with both DeepSeek and MiniMax.

### 💬 QQ integration
- Connects to QQ through [AstrBot](https://github.com/AstrBotDevs/AstrBot) + [NapCat](https://github.com/NapNeko/NapCatQQ). The bridge plugin lives in [integrations/astrbot_plugin_aicompanion](integrations/astrbot_plugin_aicompanion).
- She is the same person on QQ and on the desktop, sharing persona and memory. She tells apart her owner, group members and strangers, and keeps private matters out of group chats.
- One-click setup in Settings: pinned versions are downloaded from official sources, verified, and connected on localhost only.

### 🔐 Security and privacy
- API keys are encrypted with Windows DPAPI; the database only stores references. Keys are never shared between providers.
- Chats, memories and settings live in a local SQLite database, backed up automatically before migrations.
- Daily usage and quotas are tracked locally.

## 🚀 Quick start

### Requirements

- Windows 10 / 11 (x64)
- Python 3.11 or newer (developed on 3.14)
- Headphones recommended for voice calls (speaker echo cancellation is not implemented yet)

### Install and run

```powershell
git clone https://github.com/lsWorl/AICompanion.git
cd AICompanion

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .

.\.venv\Scripts\python.exe main.py
```

She appears straight on your desktop. Right-click her or double-click the tray icon to open the chat panel and settings.
Without a configured model the offline mock is used; set `AICOMPANION_OFFLINE=1` to force it.

### Optional components

| Component | Purpose | Install |
| --- | --- | --- |
| Agent | DeepSeek Harness and the official MCP SDK | `.\.venv\Scripts\python.exe -m pip install -r requirements-agent-lock.txt`, then `.\.venv\Scripts\python.exe -m pip install -e ".[agent]"`, or run `tools\setup_dsh.bat` |
| Local speech recognition | Offline SenseVoice (sherpa-onnx) | `.\.venv\Scripts\python.exe -m pip install -e ".[local-asr]"`, or run `tools\setup_local_asr.bat` |
| Art pack tools | Build and annotate character art packs | `.\.venv\Scripts\python.exe -m pip install -e ".[studio]"` |

Basic chat and the desktop pet don't need any of these.

## ⚙️ Configuring models and keys

Open **Settings → Abilities → Models & Keys** (设置 → 能力 → 模型与 Key):

1. Add an account — DeepSeek, MiniMax, Breeze Blue, or any OpenAI-compatible service — with its key.
2. Pick a model for each ability: chat, vision, search, agent, speech. Different abilities can use different providers.
3. Optionally give a character its own model; the others follow the global choice.

Keys are encrypted with Windows DPAPI; `LLM_*` values in `.env` are no longer read.
A few options such as speech recognition can still be set in `.env`; see [.env.example](.env.example).

You can test the model connection before opening the UI:

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics "Hi, please introduce yourself"
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics --no-stream
```

## 🗺️ Roadmap

The next stage follows [project plan v4](docs/project_plan_v4.md) (Chinese). The goal is to turn her from "a desktop pet that chats and works"
into **a companion with her own life and moods, who reaches out to you, and whose personality you shape.**

| # | Feature | Status |
| --- | --- | --- |
| 1 | Free model switching: choose provider and model per ability, DeepSeek and MiniMax first | 🚧 In progress (chat and agent usable) |
| 2 | Extensible persona packs: write, import, export and switch without losing memories | 🔍 In review |
| 3 | Lasting moods and little tempers: sulking, being clingy, talking back, and calming down | 🔍 In review |
| 4 | A life of her own: explores her interests, shares interesting finds, then waits for your reply | 📋 Planned |
| 5 | Really leaving: closes herself when the mood calls for it, and picks up again next launch | 📋 Planned |
| 6 | Remembering and recalling: hybrid recall, sourced summaries, whole-request budgets | 🚧 First part in review |
| 7 | Circles and long-running work: recurring group topics, promises, multi-day tasks, file hand-off | 📋 Planned |
| 8 | Spoken replies: Dafeiyu's own voice, long replies trimmed and split | 🚧 First version in review |
| 9 | Companion dates: days since you met, anniversaries she brings up on the day | 📋 Planned |

Known limitations: speaker echo cancellation, live partial transcripts, an Android client and clean-machine install testing are not done yet.
See [implementation_status.md](docs/implementation_status.md).

## 🏗️ Architecture

A single process with background threads. Stack: Python + PySide6 + SQLite, with no extra database or daemon.

```text
          ┌────────────────── Channels ──────────────────┐
          │  Desktop pet / chat panel    QQ (AstrBot + NapCat)  │
          └──────────────────────┬───────────────────────┘
                                 ▼
       ┌───────────────────── One brain ─────────────────────┐
       │  Persona · Mood · Relationships · Recall · Budget      │
       │  ChatController: one model call per reply              │
       └──────┬──────────────────┬──────────────────┬───────┘
              ▼                  ▼                  ▼
        Model routing        Expression            Agent
   DeepSeek / MiniMax    voice · stickers ·    DeepSeek Harness
   / OpenAI-compatible   motion · lip sync     + local tools & approvals
```

Five long-term constraints guide the design (see [docs/architecture-principles.md](docs/architecture-principles.md), Chinese):

1. Identity is independent of the model: changing models doesn't change who she is.
2. Persona is independent of memory: changing persona doesn't erase shared history.
3. Relationships are independent of channels: recognising the same person across channels requires identity linking and consent.
4. Expression is independent of the agent: the agent can be swapped while her voice and relationships remain.
5. Users own character shaping: personas can be freely created and switched, but cannot bypass safety boundaries.

### Project layout

```text
src/aicompanion/
  app.py                  # application bootstrap
  config.py               # configuration loading
  controllers/            # conversation orchestration (ChatController, …)
  brain/                  # memory cards, focus and purpose extraction
  domain/                 # character, mood and persona models
  llm/  providers/        # model clients and provider routing
  relationship/           # relationships and affection
  services/               # memory, reminders, proactive behaviour
  speech/  voice/         # synthesis, recognition and reading strategy
  pet/  body/  avatar/    # pet window, bone animation, appearance
  expression/             # stickers and motions
  agent/                  # agent: Harness bridge, tools, approvals
  qq/  bridge/  community/  # QQ channel and community slang
  storage/                # SQLite schema and migrations
  characters/             # bundled character packs
  ui/                     # PySide6 UI
integrations/             # AstrBot plugin
tools/                    # build, self-check and art-pack tools
tests/                    # unit tests (no network, no real models)
docs/                     # plans, task notes, implementation log
```

See [docs/module_guide.md](docs/module_guide.md) (Chinese) for a module-by-module guide.

## 🧪 Development and testing

```powershell
# Offline checks: unit tests + desktop pet self-check. Required for every PR.
tools\run_checks.bat

# Only when touching the agent: calls a real model (small cost). Reads DEEPSEEK_API_KEY from the process environment, never from .env.
tools\run_agent_checks.bat

# Build the Windows release (folder-style PyInstaller)
.\.venv\Scripts\python.exe tools\build_windows.py
```

Self-checks use a temporary database, never read your chats, and stay offline.

## 🤝 Contributing

Issues and PRs are welcome. Before you start, please read:

- [AGENTS.md](AGENTS.md): collaboration rules, branch naming (`task/<id>-<topic>`) and PR requirements
- [docs/project_plan_v4.md](docs/project_plan_v4.md): scope and priorities of the current stage
- [docs/tasks.md](docs/tasks.md): the task list — mark a task "in progress" before starting to avoid duplicate work

Ground rules:

- New features come with unit tests that need no network or real models.
- Explain the reason and license before adding a dependency, and pin its version.
- UI text, comments and docs are written in Chinese.
- After merging, append an entry to `docs/implementation_status.md`, **including what has not been verified**.
- Never commit `.env`, databases, private artwork or voice samples.

## 🙏 Acknowledgements

Whalemate stands on the shoulders of many open-source projects and community creators — thank you all.
"Dependency" means we use it at runtime; "reference" means we borrowed ideas, data formats or approaches only, without copying code
(this applies especially to GPL / AGPL projects).

### Runtime dependencies

| Project | How we use it |
| --- | --- |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) | The pet window and all UI |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Agent planning and model routing (including MiniMax) |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | Exposing the pet's tools to Harness over MCP |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | Platform integration and plugins for the QQ channel |
| [NapCat](https://github.com/NapNeko/NapCatQQ) | Bridging QQ to the OneBot protocol |
| [uv](https://github.com/astral-sh/uv) | Installing AstrBot's runtime during one-click QQ setup |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Local SenseVoice speech recognition |
| [py-webrtcvad](https://github.com/wiseman/py-webrtcvad) | Voice activity detection during calls |
| [SQLite FTS5](https://www.sqlite.org/fts5.html) | Full-text recall of older chats and memories |
| [Live2D Cubism SDK](https://www.live2d.com/), [PixiJS](https://github.com/pixijs/pixijs), [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) | Rendering imported Live2D models |

### Design references

| Project | What we learned from it |
| --- | --- |
| [Coopanion](https://github.com/Pal-AI-Lab/Coopanion) | The overall shape of a desktop companion (AGPL-3.0; design only, none of its code or art is used) |
| [surfcore](https://github.com/Mieksido/surfcore) | Decaying memory cards, full archive, purpose mechanism, a brain "with a life" |
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) | Barge-in, turn-taking, streaming segmented synthesis |
| [MaiBot](https://github.com/Mai-with-u/MaiBot) | When to speak and when to stay quiet; learning people and community language |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | Character-card fields, world info and import formats |
| [Mem0](https://github.com/mem0ai/mem0) | Updating, correcting and merging memories |
| [Graphiti](https://github.com/getzep/graphiti) | Facts with validity time and provenance |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Checkpoints, waiting for approval, pause and resume |
| [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS), [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | How to call a self-hosted voice service (API only; no models bundled) |

### AstrBot community plugins

| Project | What we learned from it |
| --- | --- |
| [astrbot_plugin_dsh](https://github.com/CCYellowStar2/astrbot_plugin_dsh) | Forwarding chat sessions and follow-ups to a local Harness |
| [astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager), [astrbot-meme-pack-index](https://github.com/anka-afk/astrbot-meme-pack-index) | Per-turn sticker parsing, candidate re-checks, and the meme-pack index protocol |
| [astrbot_plugin_say_picture](https://github.com/mjy1113451/astrbot_plugin_say_picture) | Fetching QQ avatars |
| [astrbot_plugin_self_learning](https://github.com/lateautumn233/astrbot_plugin_self_learning) | Observing members' expression habits and feedback |
| [astrbot_plugin_user_memory](https://github.com/Himukai-Kanata/astrbot_plugin_user_memory) | Per-person memory |

### The Dafeiyu community

Special thanks to the creators in the Dafeiyu fan community — her personality, catchphrases and voice all come from your shared work.

| Project | What we learned from it |
| --- | --- |
| [dsh-nekowhale-stickers](https://github.com/Loping151/dsh-nekowhale-stickers) | Source attribution for Dafeiyu stickers and the knowledge base (artwork by 可以叫我云大人 / 雾雪) |
| [bluedafeiyu](https://github.com/lmy414/bluedafeiyu) | The Dafeiyu archive site and its image-rights notes |
| [astrbot_plugin_feiyu_voice](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice), [WhaleTalk](https://github.com/Tokimorphling/WhaleTalk-) | The feel of her voice, pre-synthesised fixed lines, volume-driven lip sync |
| [dsh-whale-musume discussion](https://github.com/deepseek-ai/deepseek-harness/discussions/2262) | Community discussion of the whale-girl design |

This repository does **not** include any of the community assets above; stickers must be imported by users under their authors' licenses.
If you are an author of one of these projects and something here is inaccurate or you'd like the credit changed, please open an issue.

## 📄 License and notices

Whalemate's code, documentation and artwork are released under the [MIT License](LICENSE). Artwork means the illustrations, textures and icons under `art/`, `src/aicompanion/characters/*/sprites/`, `src/aicompanion/characters/*/rig/textures/` and `src/aicompanion/assets/icons/`. The following are **not** covered by MIT:


- `src/aicompanion/assets/live2d/vendor/live2dcubismcore.min.js`: Live2D Cubism Core, under the [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html); see [assets/live2d/LICENSE.md](src/aicompanion/assets/live2d/LICENSE.md). Normally, businesses with annual revenue of JPY 10 million or more need a separate Cubism SDK Release License to distribute it. **However, because Whalemate can import arbitrary external Live2D models, it may count as an [Expandable Application](https://www.live2d.com/en/sdk/license/expandable/).** Such applications must be reviewed by Live2D and sign a special Publication License Agreement before release, and the exemptions for individuals and small businesses **do not apply**. This project has not yet confirmed its classification with Live2D or obtained that license. Please check with Live2D yourself before distributing a build that includes Cubism Core.
- Other third-party libraries and components, under their respective licenses.
- "DeepSeek", "Claude", "ChatGPT" and related names and logos are trademarks of their respective owners, including logos that appear in the character artwork. The MIT License grants no trademark rights.

**Fan work notice**: the default character "Dafeiyu" is a fan creation based on the DeepSeek whale. This project is **not affiliated with or endorsed by** DeepSeek, Anthropic, OpenAI or any other company.

**Voice notice**: this project does not include or distribute any voice model or reference audio of a real voice actor. If you connect your own voice-cloning service, only use voices you recorded yourself or are explicitly licensed to use.

**Heads-up**: when working on tasks she can act on files on your computer. Sensitive actions always ask for your approval — please read what will be done before you confirm.

---

<div align="center">
If you like her too, a ⭐ would make her day.
</div>
