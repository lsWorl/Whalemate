<div align="center">

<img src="src/aicompanion/assets/icons/aicompanion-256.png" width="128" alt="Whalemate 图标">

# Whalemate · 鲸伴

**一只住在你 Windows 桌面上的鲸鱼少女。会动、会记事、会说话，也能帮你干活。**

简体中文 | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6?logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/状态-开发中-orange)

[功能](#-功能) · [下载安装](#-下载安装) · [卸载](#️-卸载) · [配置](#️-配置模型与-key) · [路线图](#️-路线图) · [参与贡献](#-参与贡献) · [致谢](#-致谢) · [许可](#-许可与声明)

</div>

---

Whalemate（鲸伴）是一个常驻 Windows 桌面的 AI 伙伴。默认角色鲸鲸是以 DeepSeek 鲸鱼为原型的二创少女，桌面形象优先采用本机安装的 Aboutme 大肥鱼，未安装时使用自带的自绘鲸娘。
她在桌面上动来动去，记得你们聊过的事，用自己的声音回话，会闹点小脾气，你交代的事情她也会动手去做。

和普通的聊天客户端不同，Whalemate 关心的是“她是谁”：**换了模型，她还是她**。人格、记忆、关系和渠道彼此独立，
你可以随意更换模型服务、塑造她的性格，在桌面和 QQ 上跟同一个她说话，你们的共同经历不会丢。

> [!NOTE]
> 项目在积极开发中，部分功能还没做完。下面的列表会标出哪些已经可用、哪些还在路线图上；
> 每项功能的实际状态以 [docs/implementation_status.md](docs/implementation_status.md) 为准。

## ✨ 功能

### 🐋 桌面角色
- 透明无边框的桌宠，只有角色本身响应鼠标：单击戳她、拖动拎起、双击快速输入。
- 本机安装 Aboutme 动画后，鲸鲸会挥手、伸懒腰、喝奶茶并左右走动。全屏应用在前台时自动让开。
- 角色以“角色包”的形式组织（人设、台词、模型绑定、形象），也支持导入标准 Live2D Cubism 3/4 模型（需要自己下载 Live2D Cubism Core，见下方“可选组件”）。

### 💬 聊天与表达
- 流式回复，连发几条消息会合并成一轮，可以随时打断。
- 表情包、动作和情绪参数随回复一起出现；思考过程不会出现在气泡、语音或日志里。
- 没配置模型时用离线模拟回复运行，不需要 API Key 也能先把界面跑起来。

### 🔊 语音与通话
- 回复默认带语音。“智能朗读”会挑重点念长回复，代码、路径、表格和清单不照着念。
- 语音合成可选 Breeze Blue、千问、智谱或 Windows 系统朗读；云端合成失败时自动退回本地朗读，回复照常完成。
- 语音通话：WebRTC VAD 断句，云端（千问、智谱）或本地 SenseVoice 识别，你一开口她就停下来听。
- 支持日语配音：聊天和字幕保持中文，只把念出来的句子翻译成日语。

### 🧠 记忆与关系
- 带衰减的记忆卡和归档，记得来源；你明确删除的内容不会被改个说法再记回来。
- 用 SQLite FTS5 召回更早的聊天和以前办过的事，每次请求的上下文大小有预算控制。
- 情绪由事件推动并慢慢回落：被冷落会委屈，被哄会消气。好感是长期关系，一次吵架不会清零。
- 人格包可以新建、编辑、切换、回退版本，也能导入导出（兼容 SillyTavern 角色卡的常用字段）。换人格不会清空记忆。

### 🛠️ 帮你干活
- 基于 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 规划任务，由桌宠自己的工具执行，敏感操作先问你。
- 通过 MCP 协议对接工具，服务只监听本机回环地址，令牌每次随机生成。
- 支持 DeepSeek 和 MiniMax 两条路线。

### 💬 QQ 接入
- 用 [AstrBot](https://github.com/AstrBotDevs/AstrBot) + [NapCat](https://github.com/NapNeko/NapCatQQ) 接入 QQ，桥接插件见 [integrations/astrbot_plugin_aicompanion](integrations/astrbot_plugin_aicompanion)。
- QQ 上和桌面上是同一个她，共用人格和记忆。她能分清主人、群友和陌生人，私下的事不会在群里说。
- 设置里可以一键安装：组件从官方地址下载固定版本，校验后安装，连接只开在本机。

### 🔐 安全与隐私
- API Key 用 Windows DPAPI 加密保存，数据库里只存引用；不同服务的 Key 不会互相借用。
- 聊天记录、记忆和设置都存在本地 SQLite，升级迁移前会自动备份。
- 每天的用量和额度在本地统计。

## 📦 下载安装

不用装 Python，也不用敲命令：

1. 打开 [Releases 页面](https://github.com/lsWorl/AICompanion/releases/latest)，下载 `Whalemate-v<version>-windows-x64.zip`。可以用同一页的 `SHA256SUMS.txt` 核对文件是否完整。
2. 把压缩包解压到**非系统盘**的文件夹，例如 `D:\Apps`。程序会把聊天和记忆存在非系统盘上，尽量不占 C 盘。
3. 打开解压出来的 `Whalemate` 文件夹（本例为 `D:\Apps\Whalemate`），双击 `AICompanion.exe`。
4. 第一次打开会出现新手向导，大约 3 分钟。向导会带你连接聊天模型（需要一个 DeepSeek 等服务的密钥，里面有申请步骤）、选择声音和形象。每一步都可以跳过，跳过模型这一步时用离线模拟回复先体验。

运行环境：Windows 10 / 11（x64）。语音通话时建议戴耳机，外放回声消除还没做。

想看大肥鱼动画版，可以在向导第 4 步或“设置 → 角色与形象”里安装；打包版的安装方法见 [Aboutme 接入说明](docs/aboutme-pet.md)。动画素材是社区作品，只能个人非商用，不随压缩包分发。

> [!NOTE]
> Windows 可能提示“已保护你的电脑”，因为这个程序没有购买代码签名证书。确认是从本仓库 Releases 下载的，再点“更多信息 → 仍要运行”。

## 🗑️ 卸载

运行 `Whalemate` 文件夹里的 `UninstallWhalemate.exe`。它会先列出要删除的每一项，你确认后才会删：

- 程序文件夹本身，包括本机装的大肥鱼动画；
- 聊天记录、记忆、心事和设置（数据库及其备份），里面也有加密保存的模型密钥；
- 程序自己建的表情图库、QQ 组件（NapCat、AstrBot）、语音缓存、本地语音识别模型、办事用的临时目录；
- 开机自动启动项（如果你开过）。

如果你在“设置 → 存储位置”里选过**自己的表情图库目录**，卸载时默认保留，因为里面可能有你自己收集的图片；勾选后才会一起删除。删除的内容无法恢复，想保留聊天记录的话，请先备份数据库文件。每项具体在哪里，见[存储位置说明](docs/storage-locations.md)。

## 🛠️ 从源码运行（开发者）

### 环境要求

- Windows 10 / 11（x64）
- Python 3.11 或更高（开发环境用的是 3.14）

### 安装与运行

```powershell
git clone https://github.com/lsWorl/AICompanion.git
cd AICompanion

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .

.\.venv\Scripts\python.exe main.py
```

首次启动时，鲸鲸使用程序自带的自绘鲸娘形象。按下方说明安装 Aboutme 素材并重启后，鲸鲸会改用大肥鱼动画。只有两套素材都无法使用时才显示占位形象。右键角色或双击托盘图标可以打开聊天面板和设置。
没配置模型时自动使用离线模拟回复；想强制离线，可以设置环境变量 `AICOMPANION_OFFLINE=1`。

### 可选组件

| 组件 | 用途 | 安装 |
| --- | --- | --- |
| 干活（Agent） | DeepSeek Harness 与官方 MCP SDK | `.\.venv\Scripts\python.exe -m pip install -r requirements-agent-lock.txt` 然后 `.\.venv\Scripts\python.exe -m pip install -e ".[agent]"`，或运行 `tools\setup_dsh.bat` |
| 本地语音识别 | 离线 SenseVoice（sherpa-onnx） | `.\.venv\Scripts\python.exe -m pip install -e ".[local-asr]"`，或运行 `tools\setup_local_asr.bat` |
| 导入外部 Live2D 模型 | Live2D Cubism Core（适用 Live2D 专有许可，本项目不附带） | `.\.venv\Scripts\python.exe tools\fetch_avatar.py`，从 Live2D 官网下载到本机 |
| Aboutme 大肥鱼 | 在本机为鲸鲸安装桌面动画 | 先运行 `.\.venv\Scripts\python.exe -m pip install -r tools\aboutme-requirements.txt`，再运行 `.\.venv\Scripts\python.exe tools\install_aboutme_pet.py`。打包版安装与离线重装见[接入说明](docs/aboutme-pet.md)。 |
| 形象包工具 | 编译、标注角色形象包 | `.\.venv\Scripts\python.exe -m pip install -e ".[studio]"` |

基础聊天和桌宠不需要安装这些可选依赖。Aboutme 帧动画无需 Live2D Cubism Core。

## ⚙️ 配置模型与 Key

打开 **设置 → 能力 → 模型与 Key**：

1. 添加一个账户：DeepSeek、MiniMax、Breeze Blue，或者任意 OpenAI 兼容服务，填入它的 Key。
2. 为每种能力分别选择模型：聊天、识图、搜索、办事、语音合成。不同能力可以用不同的服务。
3. 需要的话，可以给某个角色单独指定模型，其余角色跟随全局设置。

Key 用 Windows DPAPI 加密保存，`.env` 里的 `LLM_*` 已经不再读取。
语音识别等少数选项仍然可以用 `.env` 配置，模板见 [.env.example](.env.example)。

在打开界面之前，可以先测试模型连接：

```powershell
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics "你好，请简单介绍一下你自己"
.\.venv\Scripts\python.exe -m aicompanion.llm.diagnostics --no-stream
```

## 🗺️ 路线图

下一阶段的开发依据是 [项目计划书 v4](docs/project_plan_v4.md)，目标是让她从“会聊天、会办事的桌宠”变成**有自己生活、有情绪、会主动找你、性格由你塑造的伙伴**。

| # | 功能 | 状态 |
| --- | --- | --- |
| 1 | 模型自由切换：每种能力单独选服务和模型，DeepSeek、MiniMax 优先 | ✅ 已上线：聊天、识图、搜索、办事、语音合成可分别选择 |
| 2 | 可扩展人格包：自己写、导入导出、切换，不丢记忆 | ✅ 已上线（兼容 SillyTavern 角色卡，可回退版本） |
| 3 | 持续情绪与小脾气：委屈、撒娇、回怼，也会消气 | ✅ 已上线 |
| 4 | 自己的生活：按兴趣去看、去做，有趣的事主动分享，然后等你回应 | ✅ 已上线（统一主动调度 + 分享循环），等待真机长时间体验 |
| 5 | 真正退出：情绪合适时真的关掉自己，下次启动能接上 | 📋 计划中 |
| 6 | 记得住、想得起：混合召回、阶段摘要、整份请求的预算 | 🚧 第一部分已上线（全文召回、请求预算）；语义召回和阶段摘要待做 |
| 7 | 圈子与长期事务：群里的长期话题、约定、跨天办事、把文件交给她 | 📋 计划中 |
| 8 | 回复带语音：大肥鱼声线，长回复分段取舍 | ✅ 已上线（智能朗读、鲸鲸默认声线）；QQ 语音消息待做 |
| 9 | 陪伴日期：认识第几天、纪念日，到了那天自己提起 | ✅ 基础版已上线 |
| — | 界面重构：鲸鲸主题、首次启动向导、设置自动保存 | ✅ 已上线 |
| — | 解压即用的 Windows 版和卸载程序 | 🚧 进行中 |

“已上线”指已经合入主线并通过自动测试；真实模型、QQ 和语音服务的体验以负责人真机验收为准。

已知限制：外放回声消除、流式识别字幕、安卓客户端、干净 Windows 环境的安装验收还没有完成。详见 [implementation_status.md](docs/implementation_status.md)。

## 🏗️ 架构

单进程，后台工作放在线程里。技术栈：Python + PySide6 + SQLite，不依赖额外的数据库或守护进程。

```text
            ┌───────────────── 渠道 ─────────────────┐
            │  桌宠 / 聊天面板     QQ（AstrBot + NapCat） │
            └────────────────────┬───────────────────┘
                                 ▼
       ┌──────────────────── 大脑（只有一个） ───────────────────┐
       │  人格包 · 情绪 · 关系 · 记忆召回 · 上下文预算               │
       │  ChatController：一次回复只调用一次模型                   │
       └──────┬──────────────────┬──────────────────┬─────────┘
              ▼                  ▼                  ▼
          模型路由              表达层              办事（Agent）
   DeepSeek / MiniMax     语音 · 表情包 · 动作     DeepSeek Harness
     / OpenAI 兼容            · 口型              + 本地工具与审批
```

设计上遵守五条长期约束（见 [docs/architecture-principles.md](docs/architecture-principles.md)）：

1. 角色身份独立于模型：换模型不改变她是谁。
2. 人格独立于记忆：换人格不清空共同经历。
3. 关系独立于渠道：同一个人跨渠道识别需要身份关联和授权。
4. 表达独立于 Agent：Agent 可以换，她的说话方式和关系状态保留。
5. 用户拥有角色塑造权：人格可以自由创作和切换，但不能绕过安全边界。

### 目录结构

```text
src/aicompanion/
  app.py                  # 应用启动
  config.py               # 配置加载
  controllers/            # 对话流程编排（ChatController 等）
  brain/                  # 记忆卡、关注点与目的提炼
  domain/                 # 角色、情绪、人格等领域模型
  llm/  providers/        # 模型客户端与供应商路由
  relationship/           # 关系与好感
  services/               # 记忆、提醒、主动行为
  speech/  voice/         # 语音合成、识别与朗读策略
  pet/  body/  avatar/    # 桌宠窗口、骨骼动画、形象
  expression/             # 表情包与动作
  agent/                  # 干活：Harness 桥接、工具、审批
  qq/  bridge/  community/  # QQ 渠道与社区词库
  storage/                # SQLite 表结构与迁移
  characters/             # 内置角色包
  ui/                     # PySide6 界面
integrations/             # AstrBot 插件
tools/                    # 构建、自检、形象包工具
tests/                    # 单元测试（不联网、不调用真实模型）
docs/                     # 计划书、任务说明、实施记录
```

各模块的详细说明见 [docs/module_guide.md](docs/module_guide.md)。

## 🧪 开发与测试

```powershell
# 离线检查：单元测试 + 桌宠自检，每个 PR 都要跑
tools\run_checks.bat

# 改到“干活”时再跑：会调用真实模型，产生少量费用；只读进程环境里的 DEEPSEEK_API_KEY，不读 .env
tools\run_agent_checks.bat

# 打包 Windows 版本（目录式 PyInstaller）
.\.venv\Scripts\python.exe tools\build_windows.py
```

自检脚本使用临时数据库，不读取你的聊天记录，不联网。

## 🤝 参与贡献

欢迎提 Issue 和 PR。开始之前请先读一下：

- [AGENTS.md](AGENTS.md)：协作规则、分支命名（`task/<编号>-<主题>`）和 PR 要求
- [docs/project_plan_v4.md](docs/project_plan_v4.md)：当前阶段的范围和优先级
- [docs/tasks.md](docs/tasks.md)：任务清单，开工前把任务标成“进行中”，避免重复劳动

几条基本要求：

- 新功能要配单元测试，测试不依赖网络和真实模型。
- 引入新依赖前先说明理由和许可，并锁定版本。
- 界面文字、注释和文档使用中文。
- 合入后在 `docs/implementation_status.md` 末尾追加记录，**写清楚哪些还没验证**。
- 不要提交 `.env`、数据库、私人立绘或试听音频。

## 🙏 致谢

Whalemate 站在许多开源项目和社区创作者的肩膀上，在此一并感谢。下面的“怎么用的”一栏说明了我们和每个项目的关系：
**依赖**是运行时直接使用；**参考**是只借鉴了设计思路、数据格式或做法，没有复制代码（对 GPL / AGPL 项目尤其如此）。

### 运行时依赖

| 项目 | 怎么用的 |
| --- | --- |
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) | 桌宠窗口与全部界面 |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | “干活”的规划与模型路由（含 MiniMax 路由） |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 桌宠工具通过 MCP 对接 Harness |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | QQ 渠道的平台接入与插件机制 |
| [NapCat](https://github.com/NapNeko/NapCatQQ) | 把 QQ 接成 OneBot 协议 |
| [uv](https://github.com/astral-sh/uv) | QQ 一键接入时安装 AstrBot 运行环境 |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 本地 SenseVoice 语音识别 |
| [py-webrtcvad](https://github.com/wiseman/py-webrtcvad) | 通话中的语音端点检测 |
| [SQLite FTS5](https://www.sqlite.org/fts5.html) | 旧聊天与旧事的全文召回 |
| [Live2D Cubism SDK](https://www.live2d.com/)、[PixiJS](https://github.com/pixijs/pixijs)、[pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) | 导入外部 Live2D 模型时的渲染 |

### 设计参考

| 项目 | 参考了什么 |
| --- | --- |
| [Coopanion](https://github.com/Pal-AI-Lab/Coopanion) | 桌宠伙伴的整体形态（AGPL-3.0，只参考设计，不使用其代码和贴图） |
| [surfcore](https://github.com/Mieksido/surfcore) | 衰减记忆卡、全量存档、目的机制与“有生活”的大脑设计 |
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) | 语音打断、轮次判断、边生成边分段合成 |
| [MaiBot](https://github.com/Mai-with-u/MaiBot) | 什么时候开口、什么时候安静，认识人和社区语言 |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | 角色卡字段、世界书与导入格式 |
| [Mem0](https://github.com/mem0ai/mem0) | 记忆的更新、更正与合并 |
| [Graphiti](https://github.com/getzep/graphiti) | 带有效时间和来源的事实建模 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 检查点、等待审批与暂停恢复的思路 |
| [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)、[CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | 本地声线服务的接入方式（只调用接口，不打包模型） |

### AstrBot 社区插件

| 项目 | 参考了什么 |
| --- | --- |
| [astrbot_plugin_dsh](https://github.com/CCYellowStar2/astrbot_plugin_dsh) | 把聊天会话转给本机 Harness 的消息与追问适配 |
| [astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager)、[astrbot-meme-pack-index](https://github.com/anka-afk/astrbot-meme-pack-index) | 表情包的逐轮解析、候选复验与表情包索引协议 |
| [astrbot_plugin_say_picture](https://github.com/mjy1113451/astrbot_plugin_say_picture) | QQ 头像的获取方式 |
| [astrbot_plugin_self_learning](https://github.com/lateautumn233/astrbot_plugin_self_learning) | 观察群友的表达习惯和反馈 |
| [astrbot_plugin_user_memory](https://github.com/Himukai-Kanata/astrbot_plugin_user_memory) | 按人分开的记忆 |

### 大肥鱼社区

特别感谢大肥鱼同人社区的各位创作者，她的性格、口癖和声音感觉都来自大家的共同创作。

| 项目 | 参考了什么 |
| --- | --- |
| [hunmer/aboutme-pets](https://github.com/hunmer/aboutme-pets) · [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) | 鲸鲸的桌面动画取自 Aboutme 整理的 dsh / dsh1 图集，原始素材来自 dsh-pet。 |
| [dsh-nekowhale-stickers](https://github.com/Loping151/dsh-nekowhale-stickers) | 大肥鱼表情素材与知识库的来源标注（素材作者：可以叫我云大人 / 雾雪） |
| [bluedafeiyu](https://github.com/lmy414/bluedafeiyu) | 大肥鱼档案站与图片权利说明 |
| [astrbot_plugin_feiyu_voice](https://github.com/Esmerubphire/astrbot_plugin_feiyu_voice)、[WhaleTalk](https://github.com/Tokimorphling/WhaleTalk-) | 大肥鱼声线的听感、固定台词预合成、按音量驱动口型 |
| [dsh-whale-musume 讨论](https://github.com/deepseek-ai/deepseek-harness/discussions/2262) | 鲸娘形象的社区讨论 |

### 通用表情图形

| 项目 | 怎么用的 |
| --- | --- |
| [Twemoji](https://github.com/jdecked/twemoji)（Twitter, Inc. 及其他贡献者，CC BY 4.0） | 日常表情素材库 `assets/stickers/daily-reactions-v2/` 里的 62 张通用反应图：原 SVG 一并收录，用 Qt 渲染成 256×256 透明 PNG，没有重绘或加字 |

本仓库**没有**收录上述大肥鱼社区的图片素材。日常表情素材库里的 109 张鲸娘 GIF 由 `tools/prepare_daily_stickers.py` 在本机从作者网站按固定地址和哈希下载，仓库和发布包都不附带；其他表情包同样需要用户按原作者的许可自行导入。如果你是上述项目的作者，发现描述有误或希望调整署名，欢迎提 Issue。

## 📄 许可与声明

本项目代码、文档、自绘鲸娘的运行素材（角色包中的立绘和骨骼纹理）、应用图标（`src/aicompanion/assets/icons/`）及六张原创表情图片（`src/aicompanion/assets/expression/`）采用 [MIT License](LICENSE)。

- **Aboutme 大肥鱼**素材和据此生成的本机动画图集适用[上游素材声明](https://github.com/hunmer/aboutme-pets/blob/1221697ec20c2057829f9d7fcbd316fee8b3df65/pets/dsh1/NOTICE.txt)。禁止商用，公开二创须署名 PC2005-cloud/dsh-pet。上游图集仅在本机安装，仓库和发布包均不附带。
- **Live2D Cubism Core**：适用 [Live2D Proprietary Software License](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_cn.html)。本项目的仓库和打包版本都**不包含** Core，只有导入外部 Live2D 模型时才需要，由用户运行 `tools/fetch_avatar.py` 从 Live2D 官网自行下载，下载即表示同意该许可。因为能导入任意模型，带着 Core 分发本项目的构建版本可能属于 Live2D 定义的[可扩展应用](https://www.live2d.com/en/sdk/license/expandable/)，需要先经 Live2D 审核并签订专门的出版许可协议，个人和小规模企业的豁免不适用。`tools/build_windows.py` 默认会把 Core 从构建结果里去掉；`--keep-cubism-core` 只供自己电脑使用，不要对外分发。
- **Twemoji 图形**（`assets/stickers/daily-reactions-v2/images/twemoji/`、`sources/twemoji-svg/`）：Copyright Twitter, Inc. 及其他贡献者，采用 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，不适用 MIT。我们把原 SVG 渲染成 256×256 PNG，没有改动图形内容。许可全文见 [`licenses/TWEMOJI-CC-BY-4.0.txt`](assets/stickers/daily-reactions-v2/licenses/TWEMOJI-CC-BY-4.0.txt)，再分发时请保留署名和许可链接。
- **鲸娘表情 GIF**：作者“可以叫我云大人 / 雾雪”（[bilibili](https://space.bilibili.com/103739) · [kiriyuki.com](https://kiriyuki.com)）保留版权，只允许非商业、保留作者链接的分享，仓库和发布包都不附带。`catalog.json` 中鲸娘条目的说明标注参考了 [dsh-nekowhale-stickers](https://github.com/Loping151/dsh-nekowhale-stickers) 的知识库（CC BY-NC 4.0），这部分不适用 MIT，详见 [`licenses/WHALE-ASSETS.txt`](assets/stickers/daily-reactions-v2/licenses/WHALE-ASSETS.txt)。
- 其他第三方库和组件，以各自的许可证为准。
- “DeepSeek”“Claude”“ChatGPT”等名称和徽标是各自权利人的商标，其中也包括出现在角色立绘里的徽标。MIT 许可不授予任何商标权。

**同人声明**：默认角色鲸鲸采用的“大肥鱼”形象是网友围绕 DeepSeek 鲸鱼形象创作的二创角色。本项目与 DeepSeek、Anthropic、OpenAI 等公司**没有任何关联**，也没有得到它们的认可。

**声音声明**：项目不包含、也不分发任何真人声优的声音模型或参考音频。如果你接入自己的音色复刻服务，请只使用自己录制或明确获得授权的声音。

**使用提醒**：她在“干活”时可以操作你电脑上的文件。敏感操作都会先征求你的同意，请在确认前看清楚要执行的内容。

---

<div align="center">
如果你也喜欢她，欢迎点个 ⭐
</div>
