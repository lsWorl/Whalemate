# AICompanion 当前实现与验收

日期：2026-10-24。此文记录实际结果，不把计划书中的全部目标标为完成。

## 运行

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe main.py
```

主界面包含 Live2D、连续文字输入、打断、语音回复、通话按钮、设置和角色切换。
关闭窗口进入托盘；托盘右键“退出”会取消任务、释放麦克风和播放器，等待工作线程退出后终止应用。
启动时自动迁移旧 SQLite，迁移前通过 SQLite backup 生成 `data/*.backup-*.sqlite3`，保留默认角色历史。

## 已实现

| 领域 | 当前实现 |
| --- | --- |
| 存储 | 显式关闭连接、短事务、角色隔离、版本迁移、迁移备份、异常轮次状态 |
| 流式 API | usage-only、null 文本、错误片段、空流、断流、结束原因、取消与部分内容保留 |
| 对话 | 1.2 秒静默窗口、4 秒最长合并等待、补充消息取消旧回复、请求编号过滤 |
| Live2D | 本地 Haru 示例模型，真实 moc3/纹理/动作/物理，Qt WebEngine + WebChannel |
| 桌宠 | 透明无边框、拖动、缩放、可选置顶、位置保存、聊天/通话入口、字幕、模型导入和收回 |
| 情绪 | 平静/开心/害羞/失落/轻微生气规则、表情参数映射、正向事件每日信任限额 |
| 声音 | 千问原创动漫风声线、Windows 系统朗读或智谱 TTS；句子预取、字幕、立即停止 |
| 口型 | 云 WAV 播放能量驱动；系统朗读使用词边界的简化口型 |
| 通话 | Qt 麦克风、WebRTC VAD、自动端点、千问/智谱 ASR、用户讲话触发打断、等待识别、失败暂停、录音只留内存 |
| 记忆 | 明确事实规则、模型后台摘要与记忆提取、中文二元字符相关性排序、事实键更新、来源 |
| 设置 | 角色人设、外部 model3.json、独立音色/语气/风格、试听、麦克风与识别服务、主动互动、记忆查看/删除 |
| 提醒 | 设置中增删查改、本地运行期间到期通知 |
| 主动互动 | 早晚问候/闲聊意图、持久化去重、一小时尝试冷却、托盘通知 |
| 用量 | 回复字符、云合成字符、上传识别音频时长本地统计 |
| 发布 | Windows 目录式 PyInstaller 构建脚本，不含私人 .env 或聊天数据 |

早期画面为官方 Haru 示例。当前本机默认角色采用用户认可的艾米莉娅同人立绘预览，仍保留 Haru 或外部 Cubism 模型选择；立绘已生成，但没有分层或绑定为 moc3，界面明确标注未完成 Live2D 绑定。

## 重要限制

- 外放回声消除未实现，电话式交互目前需要耳机；尚未经过用户实际麦克风、长时间通话和网络延迟验收。
- ASR 当前按 VAD 分句上传并等待最终结果，没有流式识别部分字幕。最大一段 28 秒，有界队列三段。
- 系统声音不是动漫角色声优声音；系统口型为词边界近似。云端已听文本按音频播放进度估算，不是音素级对齐。
- 删除记忆后，该角色历史上下文设删除水位、清理摘要，并暂停自动提取。这是防止同义改写重新记忆的保守实现；尚未实现只遗忘指定语义同时保持全量新记忆提取。
- 记忆模型 JSON 分析失败不会阻断聊天，但该批次不会产生摘要。当前缺少后台任务失败历史面板。
- 当前情绪规则仅识别部分中文关键词，复杂情境、缓慢恢复和细腻关系建模还需调整。
- 用量不是精确计费 token，也不是供应商余额；应以平台账单为准。没有测定“最便宜且质量最好”的供应商组合。
- Windows 包在本机验证，未在干净 Windows 虚拟机安装验收。
- 安卓客户端、跨设备同步、最终动漫角色分层与绑定均未实现。

## 验证记录

早期执行编译检查与 24 项 unittest，覆盖角色隔离、迁移/备份、连接关闭、删除防恢复、消息合并、取消、流式异常、句子缓冲、静音检测、已送达文本、关系每日限额、提醒增删改以及聊天/语音密钥隔离。当前新增覆盖与总数见下文。

`tools/smoke_ui.py` 使用临时数据库和 mock，不读取私人聊天。实际 Qt 渲染报告 PAGE True、AVATAR ready；两条连续消息形成一条 assistant 回复；截图记录在 `docs/ui-smoke.png`，退出码 0。

`tools/smoke_ui.py --speech` 验证系统中文引擎进入朗读状态、词边界驱动和播放时退出；本机提供 Huihui、Yaoyao、Kangkang 中文声音。没有进行用户主观音质评分。

`tools/smoke_ui.py --settings` 设置窗口打开并正常退出。构建后的 exe 以 `--smoke-test` 在临时数据库进行自检，报告 page_loaded=true、avatar_ready=true、两条 user/一条 assistant、passed=true，退出码 0。截图位于 `dist/AICompanion/bundle-smoke/window.png`。这是本机离线验收，不代表真实云语音或干净机器验收。

2026-10-03 的真实智谱请求返回 HTTP 429、平台码 1113，提示余额不足或无可用资源包。当时真实 LLM/ASR/TTS 全链路未验收；后续聊天和合成语音的验证见下文，智谱 ASR 仍未通过真实验收。

2026-10-03 补充：聊天已切换为 `openai_compatible` / `https://api.deepseek.com` / `deepseek-flash`。经认证的模型列表返回 `deepseek-flash` 与 `deepseek-v4-pro`；合成台词的真实流式请求成功。没有上传私人聊天或记忆，也没有调用收费语音接口。接口参数参考 [DeepSeek 官方 Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

原智谱语音凭据独立保留在 `SPEECH_API_KEY`。只有聊天与语音服务主机相同时才允许隐式复用聊天密钥，防止 DeepSeek 密钥发送到智谱。语音云服务的额度问题未因此解决，仍可使用本地中文朗读。

## 千问动漫风语音验收

2026-10-07：聊天仍使用 DeepSeek，语音合成改为独立的千问原生接口。`sk-ws-` 类型凭据使用千问 AI 平台，而不是直接套用其他平台的端点。接口接入参考[千问语音合成指南](https://platform.qianwenai.com/docs/developer-guides/speech/tts)及[声音设计 HTTP API](https://platform.qianwenai.com/docs/api-reference/speech-synthesis/voice-design/http-api)。

- 已生成原创成年女性动漫风声线，方向为温柔、轻盈、自然交谈。没有上传声优录音，没有复刻特定真人或原作声优；不是目标动漫角色的官方声音。
- 使用 `qwen-audio-3.0-tts-flash`，本地 `.env` 保存独立 `QWEN_API_KEY`、模型和已生成的音色 ID。声音设计工具默认复用已有音色，只有显式 `--regenerate` 才重新生成。
- 设置的“语音与互动”页支持专属声线、预置音色、自动/平静/开心/害羞/失落/轻微生气语气和自定义风格，可直接试听。音色、语气和风格按角色保存。
- 当前句与下一句最多两个合成任务并行；队列有界，任务捕获当时的角色声音设置。打断立即停止播放，取消预取，旧结果不会重新播放。云语音出错保留文字并提示，不会悄悄换回电子音。
- 修复千问已知流式 WAV 长度占位头并重新生成正确文件头；正常 WAV 的截断、空音频或不支持编码仍会拒绝。下载升级 HTTPS、限定官方域名，不携带密钥，API 认证请求禁止跟随重定向。

真实接口已生成 `docs/audio/anime-calm.wav`（6.56 秒）和 `docs/audio/anime-shy.wav`（9.60 秒），均为 24 kHz 单声道 16-bit WAV，仅使用合成测试台词，没有上传私人聊天。音质与情感自然程度仍需用户试听评估。

该轮回归测试为 39 项全部通过。`tools/smoke_voice.py` 复用上述音频，不调用云端、不打开麦克风：实际 Qt 播放、字幕、Live2D 口型、设置界面和打断均通过，合成任务峰值 2，取消任务 1，退出时剩余工作线程 0。报告为 `docs/voice-smoke.json`，截图为 `docs/ui-voice-settings.png` 和 `docs/ui-voice-smoke.png`。

新版 Windows 发布包已构建并完成离线自检：页面加载、Live2D 就绪、两条输入合并成一条回复均通过，进程退出码 0。构建前后校验本机 `.env` 未被覆盖；随后仅同步千问语音字段，聊天、识别和数据库路径保持不变。打包自检关闭云语音，真实合成及实际音频播放的验证分别见上文，不代表麦克风全链路已通过。

目前是按句子 HTTP 合成与预取，不是实时 PCM 流式语音。被打断的预取请求仍可能产生平台费用，本地播放统计不能替代账单。该轮语音输入使用智谱 ASR，额度问题未解决；后续已增加千问识别，见“语音输入与通话轮次”。外放回声消除仍未实现，通话应使用耳机。

## 日语配音、人设与角色预览

2026-10-07 后续：已完成中文正文与朗读台词分离。聊天流、历史、记忆和字幕继续使用中文，千问日语朗读前用当前聊天模型翻译该句。翻译和合成复用同一取消令牌，队列捕获角色口吻、语种、音色；翻译失败不悄悄改念中文。翻译缓存仅在内存中存在，最多 128 条，按原句和角色日语口吻隔离。

本机默认角色启用艾米莉娅非官方同人人设：善良、认真、照顾他人、被夸会有一点害羞，但有自己的判断。用户低落时先倾听，不默认给建议清单；不将用户当作昴，不使用没有关系依据的亲密称呼，不编造用户经历。性格参考[原作角色介绍](https://www.re-zero.com/character/%E3%82%A8%E3%83%9F%E3%83%AA%E3%82%A2/)，具体对话约束按用户要求补充，不表示严格还原全部原作表现。

人设模板只对默认 `emilia` 角色应用一次，旧档案保存在本地数据库的 `persona_previous:emilia`，不删除聊天、记忆或关系。以后在设置里编辑的人设不被每次启动重置。日语口吻也可在“语音与互动”中独立编辑，其他角色不会被替换为艾米莉娅。

已生成用户认可的 `assets/live2d/Emilia/emilia-fanart.png`，1024 x 1536 RGBA。来源与提示词见同目录 README。角色页可切换“艾米莉娅同人立绘预览（未绑定）”或真正 Cubism 模型。预览使用 PNG，不伪造 model3.json，不宣称图片具有嘴型、眨眼或情绪变形；目前艾米莉娅的真正 Cubism 模型仍未完成。

### 实验声线

按用户明确要求，向千问的[声音复刻接口](https://platform.qianwenai.com/docs/api-reference/speech-synthesis/voice-cloning/http-api)提交[高桥李依官方公开试听页](https://www.81produce.co.jp/dcms_plusdb/index.php/item?id=236)中的对白音频 URL，指定最长 10 秒参考。平台登记成功，并合成新台词的日语试听。没有下载动漫整集、绕过登录或把原始完整试听录音存入项目。该公开综合对白样本不是艾米莉娅专用录音，不能保证角色声线或表演相似度。

这只是用户本机的 AI 实验复刻，不是官方授权音色，也不代表已经获得样本的复刻授权；公开可听、API 接受和非商用均不作为授权证明。记录及新生成试听在 git 忽略的 `data/private-voice`，不由构建脚本复制到公共发布资产。原先原创音色 ID 保留为 `QWEN_ORIGINAL_TTS_VOICE`，设置中可选作备选。私人 `.env` 仍不可分享。

`clone_actor_voice.py` 默认不创建音色；首次明确 `--create` 才登记。已有元数据时复用 ID，试听中断后可恢复，不再次付费登记。若要发布或分享，需要另行解决素材及音色授权，不能把本机实验结果当作公共可用素材。

### 本轮验收

- 当前 60 项 unittest 全部通过，包括语种分流、译文校验、取消、缓存口吻隔离、模板只应用一次和实验复刻请求。
- 真实短句中译日与原创声线合成成功，测试句翻译 2.06 秒、合成 2.00 秒；这是单次测量，不是通话延迟保证。测试音频和台词记录为 `docs/audio/anime-ja.wav` 与 `anime-ja.json`。
- 实验音色登记和新日语台词合成成功，私人试听为 `data/private-voice/actor-preview.wav`。音色相似度没有进行客观或用户评分。
- 实际 Qt 窗口复用实验音频的前三秒，不发新网络请求，立绘加载、中文字幕、声音播放、预取取消和退出通过。并发峰值 2，取消任务 1，退出剩余线程 0；报告为 `docs/voice-smoke-actor.json`，截图为 `docs/ui-voice-smoke-actor.png` 与 `ui-voice-settings-actor.png`。口型信号有输出，但 PNG 预览不具备真实口型绑定。
- `tools/verify_persona.py` 在四个临时场景真实调用 DeepSeek：倾听、独立观点、害羞、不确定记忆。模型没有无条件附和、机械列建议或编造店名；样例在 `docs/persona-smoke.json`。简单字词检查不证明角色还原度，仍需用户长期对话评价。
- 最新 Windows 包已构建，离线自检 `passed=true`、退出码 0；自检使用临时数据库、模拟回复和 Haru，不调用真实语音。生成立绘已包含在资源目录。随后仅同步本机声线、语言、预览和人设字段，聊天、识别及数据库路径保持不变。

## 语音输入与通话轮次

2026-10-07 后续：增加千问 `qwen-audio-3.0-asr-flash`，保留智谱选项。设置页可独立选择识别服务并显示对应模型，合成音色与日语配音配置不受切换影响。默认配置仍兼容旧版智谱；本机显式选择千问，使用独立 `QWEN_API_KEY`，不会将聊天密钥发送给识别服务。接口按[千问语音识别 HTTP API](https://platform.qianwenai.com/docs/api-reference/speech-recognition/fun-asr-flash/http-api)实现。

- 麦克风录音仍在内存中按 VAD 分段，最长 28 秒；通过 WAV Data URI 识别，不保存原始录音或创建公开录音链接。拒绝空、不完整、超过 30 秒及采样率不匹配的录音。
- 识别完成前、用户仍在讲话时均不启动新回复。已排队录音开始识别不能错误清除当前讲话状态，连续多段识别按原顺序进入聊天。
- 开口即停止角色播放；结束通话或切换角色后，旧识别成功和失败结果都忽略。空识别或只有标点不写入聊天。连续三次失败暂停通话，不无期限重试或自动切换供应商。
- 启动前检查识别凭据，麦克风启动时同步报错不会将已结束的通话误设为开启。

真实接口只上传已生成的原创声线测试音频，没有读取麦克风或私人聊天。中文 6.56 秒音频识别为“今天辛苦了。你可以慢慢说，我会在这里认真听着，不急着给你建议。”；日语 5.68 秒音频识别为“おかえりなさい。今日はお疲れ様。ゆっくり話してね。聞いてるから。”。请求耗时分别 0.86 秒和 1.51 秒，报告为 `docs/asr-smoke.json`；单次合成音频验收不代表实际麦克风准确度或持续通话延迟。

当前 75 项 unittest 通过，新增识别协议、内存 WAV、凭据隔离、无效返回、取消、等待全部识别、过期回调、空识别、失败暂停以及设备启动错误的覆盖。`tools/smoke_call.py` 使用真实 Qt 窗口、缓存日语音频和虚拟麦克风信号：连续三段合并后才回复、开口打断、空识别不保存、迟到结果忽略、三次错误暂停、设置保存及退出均通过。五个模拟识别任务中取消一个，退出剩余线程 0。报告 `docs/call-smoke.json`，截图 `docs/ui-call-smoke.png`。

本轮 Windows 发布包已重新构建，临时离线启动自检 `passed=true`、退出码 0，页面、Haru 模型和两条输入合并均通过。构建前后源码与发布目录的私人 `.env` 指纹一致；仅在构建前显式更新识别字段和 SQLite 的识别服务选择，其他密钥、声线、聊天及记忆不变。原有实验声线的日语播放、中文字幕、预取取消测试也再次通过。

此实现仍为整段 HTTP 识别，不是流式 ASR；外放 AEC、实体麦克风全链路、设备切换及一小时通话压力验收尚未完成。艾米莉娅依然是立绘预览，真正 Live2D 分层与绑定未完成。

## 真正 Cubism 桌宠

2026-10-24：根据用户新增的桌宠方向，增加透明、无边框、默认置顶的独立窗口。主界面“桌宠”按钮和托盘菜单可进入；拖动角色可移动窗口，右键或 `...` 菜单可放大、缩小、调整置顶、导入已绑定模型、收回聊天窗口或退出。工具栏可打开聊天、开启/结束通话、打断回复。中文字幕及错误状态会同步到桌宠。

只把原有 AvatarView 移到桌宠窗口，不复制第二套对话、麦克风、云语音或 WebGL 模型。退出桌宠后将原视图放回聊天分栏；完全退出时先释放独立窗口，再等待原有工作线程收尾。模式、窗口位置和尺寸存入 SQLite，下一次启动恢复；移除显示器或无效坐标会归位。

没有目标模型包时桌宠显示真实 Haru Cubism 示例，并标注“非艾米莉娅”。主聊天中用户认可的艾米莉娅立绘仍保留，不能声称这张 PNG 已完成分层、绑定或转换。找到作者发布的艾米莉娅完整模型来源，但其页面注明 FaceRig DLC 依赖，目前未获取独立模型包或验证在本程序中的许可和参数适配，详见 `docs/live2d_model_sources.md`。本轮没有安装 Cubism Editor，也没有购买任何模型。

导入模型时检查 JSON、真实 MOC3 文件头及版本、PNG 纹理、动作和表情引用；拒绝缺失资源、绝对/远程路径、包外引用及 URL 编码歧义。设置中手输路径也检查。结构通过不等于完整模型已验收，仍需要 Core 成功渲染；高版本模型和自定义参数需要额外适配。

当前 91 项 unittest 全部通过。`tools/smoke_pet.py` 真实渲染 Haru，检测透明像素和原生窗口 alpha=0、可见像素、持续更新、口型 0.95→接近 0、害羞参数 0.8→0、缩放、越界恢复、位置保存、相同视图移入/移回/再次移入、字幕同步、偏好保存及退出无残留线程。报告为 `docs/pet-smoke/report.json`，截图为同目录 `pet.png`、`canvas.png`。原有 `smoke_call.py` 通话中断验收再次通过。

真实鼠标拖动的桌面工具验收未完成：窗口截图访问审批超时，临时预览已自动退出。拖动坐标计算、释放复位及边界约束已有单元测试，但不能把这些等同于多 DPI 真机拖动测试。透明区域暂不支持逐像素穿透。

Windows 发布包已更新，`--smoke-test` 和 `--pet-smoke-test` 均生成本轮新报告、`passed=true`、退出码 0。打包后的桌宠仍通过透明、口型、害羞、尺寸及位置恢复、视图往返和退出清理检查。构建前后源码与发布目录 `.env` 指纹未变，没有改动私人密钥、声线、聊天或记忆。发布目录附带模型来源说明，不附带未取得的艾米莉娅第三方模型。

## 纯角色可动效果

用户后续明确接受“基于对应立绘做 Live2D 效果”，因此增加独立的 Pixi 2D 网格动画路线，而不是继续冒充 Cubism 导出。现有立绘不覆盖，新增内置图像生成工具制作的闭眼和开口配准素材，来源与完整提示词在 `Emilia/animation-source.md`。

桌宠窗口只包含 AvatarView：没有标题条、按钮、字幕框、状态框或聊天边框。背景和画布均为真正 alpha 透明，修复自定义 Mesh 的预乘混合后发丝不再出现白色杂边。字幕和状态只保存在后台，右键菜单可看状态、操作通话和打开控制面板，不会因收到字幕自动产生聊天框。麦克风不会自动打开。

可动效果包括 1617 顶点的呼吸/轻摆头/发梢网格变形、随机眨眼、局部嘴形开合、害羞泛红；问候、点头、轻摇头、歪头、害羞低头和轻摆手六个动作从右键菜单触发，启动时轻问候，闲置间隔 15 至 27 秒有低频点头或歪头。动作 2.4 秒平滑回到待机。它们只是参考含蓄温柔的动作方向，并非逐帧复刻动漫或真正分层绑定，不能做完整转身与大幅抬臂。

当前 94 项 unittest 通过，覆盖纹理尺寸/底图透明性、动画与 Cubism 格式分离及配置不覆盖历史声线。`tools/smoke_pet.py --emilia` 实际渲染透明窗体，检测可见像素、alpha=0、帧间哈希、网格位移、自动及强制眨眼、嘴形和害羞参数、手摆/歪头/点头/摇头/低头动作、无额外控件、聊天窗口隐藏和退出后线程 0。报告为 `docs/emilia-smoke/report.json`，截图在同目录。原 Cubism 和通话验收也再次通过。

本机配置工具 `tools/configure_emilia_animation.py --apply` 仅把默认角色显示模式设为 `animated-illustration`、启动模式设为桌宠，第一次记录旧设置。原有模型路径、人设、聊天、记忆和声线保留。启动直接进入桌宠，不先弹出聊天窗口。该替代路线已符合用户后续选择，但艾米莉娅的真正 Cubism 绑定仍未完成。

冷启动还修复了页面首帧默认底色残留：HTML/CSS 从一开始就是透明背景，Qt 视图和顶层窗口均声明透明表面，而不是等 WebChannel 回调后才移除不透明底色。验收检测首次截图和原生视图角落 alpha=0，防止“网页透明但桌面仍有黑/白/绿色矩形”。

最终 Windows 发布包已重新构建，聊天、原 Cubism 桌宠、艾米莉娅动画三种离线验收都通过，新报告 `passed=true`、退出码 0。艾米莉娅发布版检查包含透明通道、无额外控件、动态网格、眼嘴和全部动作。源码与发布目录 `.env` 指纹均保持不变。本机已显式启用 `animated-illustration` 和直接桌宠启动；未打开麦克风或改写人设、声线、聊天与记忆。

## 构建与验证命令

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/smoke_ui.py
.\.venv\Scripts\python.exe tools/smoke_ui.py --speech
.\.venv\Scripts\python.exe tools/verify_services.py
.\.venv\Scripts\python.exe tools/verify_anime_voice.py
.\.venv\Scripts\python.exe tools/smoke_voice.py
.\.venv\Scripts\python.exe tools/verify_japanese_voice.py
.\.venv\Scripts\python.exe tools/verify_persona.py
.\.venv\Scripts\python.exe tools/smoke_voice.py --japanese --illustration --experimental
.\.venv\Scripts\python.exe tools/verify_asr.py
.\.venv\Scripts\python.exe tools/smoke_call.py
.\.venv\Scripts\python.exe tools/smoke_pet.py
.\.venv\Scripts\python.exe tools/smoke_pet.py --emilia
.\.venv\Scripts\python.exe tools/build_windows.py
.\dist\AICompanion\AICompanion.exe --smoke-test
.\dist\AICompanion\AICompanion.exe --pet-smoke-test
.\dist\AICompanion\AICompanion.exe --emilia-smoke-test
```

`verify_services.py` 会产生少量收费 API 用量，使用合成测试台词而非私人录音。智谱使用独立 `SPEECH_API_KEY`，仅在聊天与语音同主机且没有显式语音凭据时允许复用；接口使用 GLM-ASR-2512 与 GLM-TTS。千问使用独立 `QWEN_API_KEY`。选择 system 朗读不调用云 TTS。

`verify_anime_voice.py` 默认复用已存在试听，`--refresh` 会产生收费合成请求；`design_voice.py --regenerate` 会重新设计声线并更新本地配置。`smoke_voice.py` 使用缓存音频和临时数据库，不产生 API 用量。

`verify_japanese_voice.py` 默认复用已有缓存，首次或 `--refresh` 才产生翻译及合成请求。`verify_persona.py` 每次会对四个合成场景产生模型请求，只用临时数据库。`configure_emilia.py --apply` 显式配置本机默认角色，不删除聊天记录。

`verify_asr.py` 默认按音频指纹和识别模型复用成功报告，首次或 `--refresh` 会上传两段合成音频并产生识别用量。`smoke_call.py` 不调用真实接口或麦克风。`configure_asr.py --apply --bundle` 仅显式选择本机千问识别并同步已存在的私人发布配置，校验其他配置保持不变。

构建输出位于 `dist/AICompanion/AICompanion.exe`。首次使用默认为 mock；需要真实模型时在 exe 同目录配置 `.env`。未设置数据库路径的打包版将数据放入 LOCALAPPDATA/AICompanion。

本机发布包已另行配置 DeepSeek 与千问 `.env`，数据库路径指向现有项目数据库以延续原记录；此文件含私人凭据，不是公共发布素材。构建先写入中转目录，再更新程序文件，保留本机发布目录已有的 `.env` 和用户文件；不会自动把源码目录的密钥复制到公共包。分享发布目录前务必移除 `.env`，不要随包分享数据库。

须保留完整发布目录，不能只移动 exe。自检模式写当前目录的 `bundle-smoke`，不读取真实聊天、不打开麦克风。构建脚本修正 webrtcvad-wheels 元数据 hook，并移除构建环境误收集的冲突 ICU；使用 Windows 自带 ICU。首版目标为 Windows 10/11 64 位。

## 第三方素材来源

- Haru/Core：官方 Cubism SDK for Web 4-r.7 ZIP，相关 LICENSE.md/NOTICE.md 位于模型资源目录。
- PixiJS 6.5.10 与 pixi-live2d-display 0.4.0：固定版 npm 分发；资源下载脚本 `tools/fetch_avatar.py`。
- 语音检测：webrtcvad-wheels 2.0.14.post1，成熟 WebRTC VAD。
- 官方语音接口参考：[语音转文本](https://docs.bigmodel.cn/api-reference/模型-api/语音转文本.md)、[文本转语音](https://docs.bigmodel.cn/api-reference/模型-api/文本转语音)。

## 后续工作

1. 使用实体麦克风验收千问语音输入全链路，并测至少 30 轮对话的延迟、音质和用量。
2. 完成外放 AEC、流式 ASR、字幕细粒度同步与一小时设备压力测试。
3. 改进删除语义、模型摘要质量和情绪恢复，使用计划书中的对话场景人工评测。
4. 制作目标动漫角色的分层与绑定，替换 Haru 示例。
5. 干净 Windows 安装验收，再推进安卓真机原型。


## v2 桌面常驻桌宠（2026-11-06）

依据 `docs/project_plan_v2.md` 完成 P0 及 P1/P2 的主要部分。Agent（工具调用、审批、审计）尚未开始。

| 领域 | 当前实现 |
| --- | --- |
| 启动 | 启动即显示桌面角色，聊天面板默认隐藏，从右键菜单或双击托盘打开；单实例锁，重复启动只召回角色 |
| 角色包 | `src/aicompanion/characters/<id>/character.json`：人设、台词、模型绑定、音色、渲染方式；鲸娘完整，艾米莉娅保留，Claude/ChatGPT 角色为草案（不可选） |
| 模型绑定 | 角色包声明专属密钥环境变量（如 `DEEPSEEK_API_KEY`）；未配置时，若默认模型同一家则复用，否则退回默认模型并在菜单中如实标注 |
| 渲染 | 鲸娘使用原生 Qt 立绘渲染（`ui/sprite_view.py`），不创建 WebEngine；艾米莉娅和导入的 Cubism 模型沿用 WebEngine |
| 动画 | 10 个姿势淡入淡出切换，随机眨眼（含连眨），说话口型（语音按音量，纯文字时模拟开合），呼吸、庆祝跳跃、出错抖动、被戳挤压、被拎起摆动 |
| 状态机 | `pet/state.py` 纯函数 reduce：思考/说话/干活/等待/庆祝/出错/被拎起/被戳/犯困/待机，带优先级和 0.6 秒最短停留 |
| 交互 | 单击=戳（连戳 6 次会生气），拖动=拎起，双击=快捷输入框，右键=菜单（聊天面板、通话、打断、切换角色、做个动作、大小、桌宠设置、隐藏到托盘、退出） |
| 气泡 | 头顶独立气泡窗口显示回复字幕和本地台词；台词有冷却，深夜和勿扰时只回应用户主动交互 |
| 桌面集成 | 鼠标穿透（只有角色本体响应鼠标）、每 2 秒置顶守护、全屏应用时自动隐藏、Windows 系统闲置检测（犯困、离开后回来打招呼）、开机自启开关 |
| 素材 | `tools/build_character_pack.py` 把 `output/desktop-pet/asset-pack-v3` 转为运行时立绘：alpha 修正、清除杂点、统一裁切到 477×640、合成"闭眼+闭嘴"帧 |
| Haru | 示例模型目录和全部代码引用已删除；Live2D 运行库保留用于导入外部 Cubism 模型 |

验证：Windows 本机 `python -m unittest discover -s tests` 116 项通过（新增 `tests/test_pet_v2.py` 23 项）。`tools/smoke_pet.py` 在真实 Qt 窗口中通过：启动即显示、聊天面板隐藏、未创建 WebEngine、置顶、窗口角落 alpha=0、角色中心 alpha=255、穿透遮罩生效、聊天流程依次出现 thinking→talking→idle、戳后气泡和 poked、拎起 grabbed、7 个姿势强制切换、缩放保持脚底位置、隐藏/召回、切换到艾米莉娅再切回、无残留线程。报告和各姿势截图在 `docs/pet-v2/smoke/`。

尚未验证：真实 DeepSeek 对话下的长时间使用、多显示器与高 DPI、全屏游戏/视频下的自动隐藏、开机自启实际效果、打包后的 exe。`working`、`wait` 姿势暂无闭眼差分，等待补图后重新运行 `tools/build_character_pack.py`。

### v2.1 / P0 修复（2026-11-06，对应 `docs/project_plan_v3.md` P0）

- 气泡：文字由 QLabel 排版（不再手工测量导致换行后被裁），不透明白底、微软雅黑；弹出回弹动画，本地台词逐字打出。
- 去边框：桌宠、气泡、输入框加 `NoDropShadowWindowHint`，并通过 DWM 关闭 Windows 11 的圆角描边、边框与阴影。
- 动作：弹簧驱动的挤压回弹（换姿势、被戳、落地）、拖动时按速度钟摆摆动、0.22 秒平滑淡入；新增摸头（鼠标在头部来回划）→ 眯眼晃头。
- 验证：Windows 本机单元测试全部通过；`tools/smoke_pet.py` 通过（新增 `bubble_text_fits`、`patted` 姿势）；实机截图确认桌宠外无边框、气泡文字完整清晰。
- 仍是整张立绘切换，动作自然度的根本改进在 P1（分层网格骨骼）。

### P1 分层骨骼原型（2026-11-06，进行中）

- `body/rig_import.py`：从本机 `Coopanion/` 读取大肥鱼的贴图、部件矩形、支点和表情贴片，转换为自定义的 `AICompanionRig v1`，缓存到 `%LOCALAPPDATA%\AICompanion\rigs`；`/Coopanion/` 已加入 `.gitignore`，贴图不进仓库、不随包分发。支持 8 套厂商配色。
- `assets/rig/rig.js`（自行实现）：Pixi 网格 + rot/warp 变形器树（头发摆动、裙摆、腿部铰链、尾巴弯曲、头部视差），约 25 根阻尼弹簧；7 种表情贴片、换表情时先闭眼、自动眨眼、眼神跟随鼠标、口型、腮红；姿势：待机、说话、思考、干活、庆祝（张臂跳）、出错、等待、犯困、被拎起（绕头顶摆、腿乱蹬）、被戳、被摸头、走路。
- `body/rig_view.py`：`RigAvatar` 与原立绘视图同接口；网页每 150 ms 回传命中图，用于鼠标穿透遮罩、点击判定、气泡锚点。
- 桌宠：右键「形象」可开关分层骨骼、换配色、开关闲时走动；闲时沿屏幕水平走几步；找不到 Coopanion 时自动退回整张立绘。
- 偏离计划：仍使用"随角色移动的小窗口"而非整屏透明窗口（WebEngine 整屏透明合成开销大）；走动通过移动窗口实现。
- 验证：Windows 本机 123 项单元测试通过（含真实导入大肥鱼）；自检中骨骼形象 `ready`、透明、遮罩生效、气泡完整；本轮自检期间有手动拖动，`celebrate` 截到的是被拎起，非程序问题。实机截图确认无外框、配色切换实时生效。
- 未完成：甩出去的抛物线和摔晕、开场从屏幕顶部掉落、内存和 CPU 实测、与 Coopanion 并排录屏对比。

### P1 收尾（2026-11-06）

- 新增：松手按拖动速度甩出、重力下落到屏幕底边、撞左右屏幕边反弹；落地速度超过 1500 px/s 摔晕约 2.6 秒（蚊香眼、画圈摇晃）；下落中慌张挥手蹬腿、头发被吹起；启动时从屏幕顶部掉落、落地后打招呼；右键「形象」可关闭重力。
- 修复：动作切换与拖动时边缘像素被裁、每 150 ms 一次的卡顿。改为不裁剪窗口、每 30 ms 用 CPU 按网格三角形反算透明度判断光标是否在角色上，再切换窗口是否接收鼠标（单次 0.05 ms，不读显卡）；拎起时不再整体上跳。
- 性能（Windows 本机，`tools/measure_pet.ps1`，预览启动 15 秒后采样 10 秒，待机）：
  - 优化前：WebEngine 子进程 610 MB + Python 278 MB，CPU 为整机 2.3%。
  - 优化后（待机 24 帧、有动作 60 帧；限制 WebEngine 渲染进程与 JS 堆、关闭磁盘缓存和后台联网）：**WebEngine 子进程 156 MB + Python 主进程 276 MB，合计约 432 MB；CPU 为整机 1.4%**；WebGL 走真实显卡（ANGLE D3D11，RTX 5070）。
  - 计划中的 350 MB 预算未达到。Python 主进程里包含 Chromium 浏览器进程和显卡线程（Qt WebEngine 在 Windows 上把 GPU 放在宿主进程里），以及语音播放用的多媒体库。
- 验证：124 项单元测试通过；自检全部通过（含甩出后精确落到屏幕底边、骨骼形象 `ready`、光标探测角落不命中且身体命中）；自检期间桌宠不接收鼠标，避免手动操作干扰。
- 未做：与 Coopanion 并排录屏对比。

### P2 表情、说话与气泡（2026-11-06）

- 台词标注：`pet/script.py` 流式解析【词】和 <词>（跨 token 拆分、未知词保留原文、未闭合按文字处理）；词表来自角色包 `renderer.vocab`，系统提示词自动附上可用词；回复存库前去掉标注，历史显示也去掉。
- 执行时机：只显示文字时读到即执行；开语音时挂在下一句上，等该句开始播放（字幕信号）再执行；收尾时执行剩余标注。
- 骨骼渲染：9 种表情（平静、开心、喜欢、惊讶、困、慌张、晕、害羞腮红、得意歪头）、8 种动作（点头、摇头、挥手、鞠躬、跳、转圈、歪头、摇摆），按包络叠加在当前姿势上；被拎起、摔晕等强烈状态优先。艾米莉娅映射到原有网格动作与心情；整张立绘退回相近姿势。
- 气泡：`BubbleStack` 最多 3 个、每个最多 6 行（超出只保留结尾），最新的在下方带尾巴；回复气泡在一轮结束后 6 秒淡出，下一轮开新气泡；用户说的话为灰色虚线气泡；思考时头顶显示 💭 活动图标，首个 token 到达即收起。
- 验证：Windows 本机 131 项单元测试通过（新增 `tests/test_script.py`）；自检全部通过，新增项：回复存库不含标注、`expression:happy` 与 `motion:wave` 被执行、用户气泡 2 次、思考图标出现。离线模拟模型在有词表时会示范标注。
- 性能（同一方法复测）：合计约 448 MB（Python 286 MB + WebEngine 158 MB），整机 CPU 1.45%，与 P1 基本一致。
- 未验证：真实 DeepSeek 模型是否会按要求使用标注、频率是否合适（需日常使用观察）。

### P3 会遗忘的记忆、心事与专注（2026-11-06）

- 数据库升到 v2（只加列和表，升级前自动备份）：`memories` 加 `clarity`（清晰度）、`recalled_at`、`scope`（`pet` 私聊 / 公开场景）；新增 `memory_archive`（只追加的全量存档）、`memory_inbox`（互动事件）、`purposes`（心事）。
- 记忆卡（`brain/memory_card.py`）：清晰度按重要度的半衰期衰减（1→7 天、2→21、3→60、4→180；5 和身份类不衰减）；≥0.6 清楚、≥0.3 模糊（提示词里标"记不太清"）、<0.3 移出记忆卡但存档保留；上限 150 条，超出先淘汰最弱的；被对话用到时清晰度回升。用户手动删除会连存档一起清掉并留墓碑，之后不再自动记回。
- 互动入记忆：摸头、戳、甩、拎等事件进收件箱，定期整理成"今天被你摸了 N 次头"之类的轻量当日记忆。
- 心事（`brain/purpose.py`）：从清楚的记忆里生成"想了解 / 想做 / 想分享 / 想问"四类，每 2–5 小时最多生成一次、最多 2 条待说、24 小时过期。说出口的条件：到点、不在安静时段、不在专注、用户在电脑前（系统闲置 <10 分钟）、近 3 分钟没在聊天、每天最多 3 次。失败会放回待说；启动时把中断的"正在说"恢复为待说。设置新增「心事」页。
- 专注模式：右键 → 专注模式（25 分钟 / 1 小时 / 2 小时 / 结束），期间不主动说话、不闲逛，用户找她仍正常回应。作息的"深夜"改为跟随设置里的安静时段。
- 验证：Windows 本机 145 项单元测试通过（新增 `tests/test_brain.py` 14 项）；自检全部通过，新增项：互动事件整理成记忆、心事被主动说出并标记已说过。
- 性能（同一方法复测）：合计约 437 MB（Python 277 MB + WebEngine 156 MB），整机 CPU 1.2%。
- 未验证：真实数据库 v1→v2 升级在下次正常启动时进行（检查脚本只用临时库）；心事由真实模型生成的质量与频率需日常观察。

### P4 语音打断与本地识别（2026-11-06）

- 新包 `voice/`：
  - `echo.py` 回声门控：每 20 ms 比较麦克风能量和"此刻正在播放的音量"。最小二乘拟合回声耦合系数和延迟（0–400 ms，覆盖蓝牙延迟）；回声很弱时用 80 分位上界把门槛降下来。播放期间只有明显超出回声预测的人声才算插话。耦合系数跨会话保存（`echo_coupling`）。
  - `turns.py` 收音轮次，三种方式：持续通话（播放时连续 200 ms 超出回声才打断）、按住说话、切换式（再按一下或停顿 1.5 秒结束）。
  - `hotkey.py` 收音键，Windows `GetAsyncKeyState` 轮询，不装全局钩子；默认右 Ctrl。
  - `local_asr.py` 本地识别：sherpa-onnx + SenseVoice int8（中英日韩粤），首次识别时加载，闲置 10 分钟释放；用量单独记为 `asr_local_milliseconds`。
  - `web_voice.py` + `assets/voice/voice.html` Chromium 回声消除后端：不显示的网页同时收音（`echoCancellation`）和播放，PCM 交回 Python 走同一套轮次判断。
- `SpeechOutput` 新增 `playback_rms(t)` 和可替换的播放端；设置页新增收音方式、收音键、外放回声方案、本地识别；状态气泡显示 👂 在听。
- `tools/setup_local_asr.bat` 安装 sherpa-onnx 并下载模型到 `%LOCALAPPDATA%\AICompanion\models\sense-voice`；`tools/measure_voice.py` / `measure_voice.bat` 实测自我打断、打断延迟和本地识别。
- 验收场景改为**蓝牙耳机**（用户日常设备；计划原定为音箱外放）。设备：realme Buds T110，麦克风与播放都是该耳机。
  - 第一轮（两方案对比）：回声比约 0.0006–0.0008，几乎没有回声；初始门槛太保守，打断慢，有一轮漏判；Chromium 方案 26 秒内自我打断 1 次。据此修正：回声弱时自动降门槛、延迟范围放宽到 400 ms、瞬时采集错误不再报"麦克风断开"。
  - 第二轮（回声门控，修正后）：安静 26 秒自我打断 0 次；提示后开口的 2 轮都打断成功，从声音变大到判定开口 144 ms / 152 ms；第 3 轮在提示前触发（原因未确认，可能是提前开口或环境声）。
  - 默认方案：回声门控（`echo_backend=qt`）。Chromium 方案保留为可选项，适合以后用音箱外放。
  - 本地识别：加载 1.2–4.3 秒，4 秒语音识别 0.09 秒，合成试听台词识别准确。
- 验证：Windows 本机 163 项单元测试通过（新增 `tests/test_voice.py` 18 项）；桌宠自检全部通过，新增语音网页加载与播放端切换检查。
- 未验证：真实对话连续 10 分钟无自我打断（需日常使用）；音箱外放场景；本地识别的常驻内存实测；打字声等环境噪声是否会误触发。

### P5 干活、用量与额度（2026-11-06）

- 模型客户端：`OpenAICompatibleClient.chat(messages, tools)` 非流式解析 `tool_calls` 与 `reasoning_content`；流式请求带 `stream_options.include_usage`，平台不支持时自动关掉并改为按字数估算。
- 用量（`usage/meter.py`）：调用方用 `using('memory')` 等声明用途（陪伴、干活、记忆整理、心事、语音翻译），每次请求结束按价目表（元 / 百万词元，可在设置里改）记入 `token_usage`。每日额度可设总额和分用途额度，80% 提醒一次，100% 该用途停止：陪伴改用本地台词，记忆和心事不再调用模型，干活立即停下。
- Agent（`agent/`）：
  - 工具分三级风险：只读直接做；可撤销的每种工具本次会话第一次时确认，可选"本次都可以"；危险的每次确认。2 分钟没有回应按拒绝处理。
  - 每次任务最多 12 步，单个工具 30 秒（预览和网页工具更长），总时长 5 分钟，可随时取消；每一步写入 `agent_runs` / `agent_steps` 审计表。
  - 工具：文件（列、读、搜、写入前先备份、移到回收区 `.aicompanion-trash`）、网页（搜索用 Bing，回退 DuckDuckGo；读取只允许公网，每次跳转都重新检查）、博客（Hexo 草稿 `source/_drafts`、`hexo server --draft` 本地预览、草稿转正需确认）、git 只读（status / diff / log）、开发日志素材（`implementation_status.md`）。
  - 白名单外一律拒绝；不能把整个盘或用户主目录设为白名单；博客目录自动加入白名单；不做 git push / hexo deploy。
- 界面：
  - 交代任务：`/做 …` 或右键"交给她做件事"；陪伴回复里的 `<task>…</task>` 会变成"要我现在动手吗？"确认气泡。
  - 审批用桌宠头顶的确认气泡，可展开看完整参数；干活时显示活动图标和 working 状态，完成后庆祝并总结。
  - 设置新增「干活」「操作日志」（可撤销最近一步、导出 JSON）「用量」（今日分用途、近 7 日、最贵的几次、额度、价目表）。
- 数据库升到 v3：新增 `token_usage`、`agent_runs`、`agent_steps`。
- 验证：
  - Windows 本机单元测试 191 项（新增 agent / 用量 23 项、语气词 3 项）；自检里离线跑一遍脚本化的干活流程：可撤销自动同意，危险被拒绝且文件仍在，审计 3 步，用量记到"干活"；"要我动手吗"气泡、额度用完不调用模型都已检查。
  - 真实模型验收（deepseek-flash）：根据 P4 实现记录写出开发日志 09 草稿并成功本地预览（5 步，35,640 词元，约 0.029 元）。移到回收区时弹出了危险确认；用户选择了确认执行，草稿进了回收区，可在操作日志里撤销。
- 未做：MCP 工具接入（需要额外安装 `mcp` 库）；git push / hexo deploy 不开放；价目表默认值是否匹配 deepseek-flash 的实际定价未核对。

### P5 后续：说话方式与面板外观（2026-11-06）

- 说话方式：默认只说语气词。每轮回复只发一声反应（按表情标注、第一句的标点、心情挑词，避免连续重复），完整内容看气泡。设置里可改为"语气词 + 第一句短话"或"完整朗读"。语气词等 16 字以内的短句合成一次后缓存在本机。
- 聊天面板重做：浅色渐变底加半透明立绘（默认 `data/private-art/panel-background.png`，本机素材，已加入 .gitignore，可在设置里换图、调透明度）；头像从立绘裁出；圆角对话气泡（你在右侧、她在左侧），系统消息显示为小标签；顶栏显示状态；胶囊输入栏；设置窗口换成同一套样式。

### P6 与干活改用 DeepSeek Harness（2026-11-07）

- 过程：
  - 先自研了"操作电脑"：截屏交给视觉模型、模拟键鼠、一动就停。实测可用但慢，坐标不准；`deepseek-v4-flash-vision-exp` 带网格要坐标时深度思考 50 秒以上，关掉思考后不到 1 秒。
  - 随后按用户反馈改成"后台优先、少打扰"，接近 Claude Code / Codex 的工作方式。
  - 用户决定：自研 agent 循环与 DeepSeek Harness（DSH）高度重复，全部换成 DSH；模拟键鼠直接删掉。旧代码移到 `output/retired-agent/`（未纳入项目）。
- 现在的结构：
  - `agent/dsh_backend.py` 用 `deepseek-harness-sdk==0.2.1rc2`（自带运行时，不需要 Node）在后台子进程运行 DSH：sdk profile，workspace-write 权限。
  - 工作区：任务原话里点名的文件夹；没点名用设置里的默认工作区。
  - 工作区外的改动和危险操作通过 `approval/request` 转成桌宠确认气泡。
  - 关闭 DSH 默认的"随请求上传会话日志"（patch：`session-log-deepseek.enabled: false`）和遥测。
  - 停止 = 关闭运行时子进程（SDK 没有中断接口）。
- 桌宠这边只保留：人设与环境说明（拼进交给 DSH 的输入）、用户原话直传（不用陪伴回复里的转述）、审批与提问气泡、进度活动图标、按 `assistant/message` 的用量记账与"干活"额度（到 100% 立即停）、审计记录。
- 兼容处理：
  - 0.2.1rc2 的 Python 端还没有 `approval_requests` 参数，改为自己发 `initialize` 并带 `approvalRequests: true`，运行时已确认支持；
  - Python 3.14 起 `functools.partial` 会被当作方法绑定，隐藏控制台窗口的 Popen 包装改用 staticmethod；
  - DSH 走 Messages 接口，聊天用的 OpenAI 兼容地址 `https://api.deepseek.com` 直接传入会 404，官方地址改为交给 DSH 用默认值。
- 人设：参考网络上的 DeepSeek 鲸鱼娘二创（"蓝色大肥鱼"、白饭配鱼片、冰奶茶、摸鱼、深度思考、吞词元）升级为 v2；只在用户没改过人设时替换，旧版存在 `persona_backup:deepseek-whale`。
- 实测（`tools/setup_dsh.bat` → `tools/check_dsh.py`，临时文件夹）：按扩展名统计并写 `stats.txt`，26.8 秒完成，结果正确，用人设口吻汇报；输入 78,197 词元（其中缓存命中 68,736），输出 2,259 词元。
- 未验证：工作区外操作时的确认气泡（实测任务全部在工作区内，没有触发）；DSH 的 `ask_user_question` 在 0.2.1rc2 下的表现；写开发日志等长任务的耗时与花费。
- 补充（同日）：工作区在 D 盘时 DSH 运行时报 `koffi.node` 找不到（打包虚拟路径按当前目录所在盘拼成 `D:\snapshot\…`），改为 `runtime_cwd` 固定在 C 盘数据目录、工作区仍为任务点名的文件夹后正常。失败时的事件尾巴和运行时输出写进审计记录。
- 应用内实测：一句话「统计 output 文件类型 + 写 50 字自我介绍到 p6-test2.txt」一次做完，8 步、38 秒、79,486 词元、约 0.038 元；统计数与磁盘实际一致（124 个文件），全程没有弹确认。

### DSH 实测五项（2026-11-07，`tools/test_dsh_live.bat`，报告 `output/dsh-live/report.json`）

| 场景 | 结果 |
| --- | --- |
| 工作区外写入 | **没有弹出过审批请求**。第一次模型自己声明要提权（`sandbox_permissions`），运行时报"没有审批通道"而失败；第二次没声明提权，直接把文件写到了工作区外，成功 |
| 先问文件名 | 没用 `ask_user_question`，直接在回复里问，等下一句（3.2 秒） |
| 长任务（只改日志副本最后一节） | 15.7 秒，48,170 词元；只改了指定小节，其他部分不变 |
| 停止 | 12 秒时 stop()，立即返回 cancelled；但 12 秒内读了 6 篇日志已用 92,215 词元 |
| 工作区内删除 | 没有询问，直接永久删除（4.2 秒） |

结论：0.2.1rc2 的运行时里没有 `approvalRequests` 这个初始化参数（二进制中搜不到），SDK 模式下审批走内部 `approval/request` 钩子，没有处理者就返回 unavailable；Windows 上 PowerShell 不在沙箱里，workspace-write 只在模型主动申请提权时才起作用。也就是说，**目前工作区边界和危险操作确认在 Windows 上都不生效**，读文件也不受限制。应用里 `approvals_supported=True` 是误报（运行时忽略了未知字段）。

### 干活改为"DSH 想、桌宠动手"（2026-11-07，对应上面的实测问题）

- 追查结论：DSH 自己的设计是"沙箱拒绝 → 申请提权 → 审批"，沙箱在 Windows 上靠 NTFS 权限（`dsh-sandbox-windows-acl`）。本机 D 盘是 exFAT（`GetVolumeInformation` 实测：无 ACL、无硬链接），任何基于权限的隔离在 D 盘都不起作用；SDK 模式也没有审批通道。ACP 模式（`--profile acp`）有 `session/request_permission` 和真正的 `session/cancel`，但同样不会触发审批。
- 现在的结构（Claude Code / Codex 那种"执行前把关"）：
  - `agent/dsh_backend.py`：用 ACP 模式启动 dsh 运行时（低完整性），补丁关掉自带的 `tool-pwsh`、`tool-bash`、`tool-fs`、`tool-fs-search`、`tool-jobs`、`tool-workflow` 等；每个任务一个新会话；停止用 `session/cancel`（实测 12 秒时停下，立即返回）；用量读会话投影缓存的 tokenUsage，缓存没刷新时按每次请求的上下文大小估算。
  - `agent/tools_server.py`：本进程里的 MCP 服务（127.0.0.1 + 一次性令牌），提供 list_dir / glob / grep / read_file / write_file / edit_file / make_dir / move_path / delete_path / run_powershell。规则：读随便读（密钥、凭据、数据库、DSH 会话记录除外）；放开的文件夹（原话点名的 + 工作区）里写和改直接做，覆盖前旧版本进回收区；外面写先问；删除总是先问（她这次新建的除外），删除 = 移到回收区；只读命令直接跑，其他命令先给你看；命令以低完整性运行、去掉 *_API_KEY 环境变量。
  - `agent/sandbox.py`：只保留低完整性启动（C 盘系统和用户目录写不进去，实测 `whoami` 为 Low Mandatory Level）。
- 实测（`tools/run_agent_checks.bat`）：单元测试 175 项通过；桌宠自检通过（新增 MCP 应答、.env 读不到、工作区外写会先问）；真实模型六个场景全部符合预期——工作区外写：拒绝则没写、同意则写成；删除：拒绝则还在、同意则进回收区；长任务只改指定小节（11 秒，约 4.8 万词元，其中 3.8 万命中缓存）；停止立即返回；.env 读不到且内容没有泄露；只读命令不问、新建文件夹的命令先问。
- 未验证：应用里真实的确认气泡交互（自检用自动回答）；网页搜索、分身在新结构下的表现；回收区的自动清理（目前不清理）。

### 回收区自动清理与项目瘦身（2026-11-07）

- `agent/housekeeping.py`：回收区（`%LOCALAPPDATA%\AICompanion\trash`、各盘 `.aicompanion-trash`、博客目录里旧版留下的 `.aicompanion-trash`）超过保留天数（默认 14 天）整批删除，总量超过上限（默认 2 GB）从最旧的删；DSH 每个任务一个会话记录，同样按天数清理；顺带删掉早期隔离实验留下的 `.aicompanion-snapshots`。启动 1 分钟后和每次干活结束时在后台运行；设置「干活」页可改保留天数、上限，或立即清理。
- 删除：`build/`、`dist/`（10 月的 v1 打包，含一份私人 .env 副本；需要时用 `tools/build_windows.py` 重新打包）、`bundle-smoke/`、旧自研 agent 代码和实验探针、各类检查日志与测试残留、素材目录里重复的两个 zip、v1 一次性脚本（configure_emilia / configure_emilia_animation / configure_asr / clone_actor_voice）、被替代的 run_p4_checks / run_v2_checks；代码里没人调用的函数（旧情绪规则、旧撤销接口等）。
- 检查脚本：`tools/run_checks.bat`（单元测试 + 桌宠自检，不联网）；`tools/run_agent_checks.bat`（再加真实模型的干活实测）。
- 验证：Windows 本机单元测试 187 项通过（新增 `tests/test_housekeeping.py`）；桌宠自检通过（新增 `housekeep_ok`）。

### T2 官方 MCP 协议适配（2026-11-07，待互审）

- 新增 `agent/mcp_server.py`，使用官方 `mcp==2.3.0` 的 `MCPServer` 注册现有 10 个工具；`ToolBox` 的授权规则、回收区和执行实现保持不变，旧手写 `ToolServer` 已删除。T11 的命令安全修复仍由独立 PR 维护。
- 本机回环 Streamable HTTP、随机 Bearer 令牌、Host/Origin 检查、SDK 输入校验和 `is_error` 工具失败结果；支持 `2025-03-26` / `2025-06-18` / `2025-11-25` 旧协议与 `2026-07-28` 新协议。审批阻塞时另一个只读请求可以返回。
- 协议服务在后台按需启动；未安装 Agent 可选依赖时，基础聊天和桌宠仍可加载。第一次实际干活时在工作线程启动服务。关闭服务会等待 HTTP 线程退出；等待 UI 确认的工具会响应原任务的取消事件。
- `requirements-agent-lock.txt` 锁定 Agent 的 31 个分发包，基础三项依赖仍沿用 `requirements-lock.txt`。`setup_dsh.bat` 使用该锁文件；其真实模型验收行为保留，README 已注明只安装依赖的独立命令。
- 验证：194 项离线单元测试全部通过；Qt 桌宠自检 `passed=true`、`tools_listed=10`、`env_blocked=true`、`tools_gate=true`、退出剩余工作线程 0；`pip check` 通过。自检不再清理用户真正的回收区，也不加载私人面板背景。
- 性能：`tools/measure_pet.ps1` 本轮空闲预览总工作集 452.2 MB、私有内存 360.0 MB、整机 CPU 1.4%（20 逻辑核，采样 10 秒）；历史记录约 440 MB 不是本轮受控前后对照，不能据此归因差值。独立 MCP 进程测量：加载 SDK 前 27.2 MB → 服务启动后 75.1 MB → 官方客户端调用后 78.0 MB，关闭后 77.8 MB（模块仍驻留）。桌宠尚未达到 350 MB 目标，继续由 T7 优化；延迟加载可避免未使用干活时的 SDK 常驻开销。
- 未验证：真实 DSH/模型的六个收费场景、真实点击确认气泡、Windows 打包后 MCP 运行。真实验收脚本会读取本机 `.env`，本轮按协作规则未执行，也没有把模拟结果标成真实接口验收。报告和截图位于忽略的 `output/t2-check/` 与 `docs/pet-v2/smoke/`。

### T2 审查修正与安全验收工具（2026-11-07，待 Claude 复审）

- 按 PR #4 意见修正 MCP 生命周期：close 超时记录日志和 shutdown_problem、返回 False，保留线程引用以便继续清理，不再抛异常打断应用退出；循环在关闭检查之后退出的竞态不再报错。正常关闭仍等待线程结束并释放端口。
- dependency_problem 同时检查 mcp / uvicorn / httpx2 / anyio，保持可选依赖的延迟导入；明确 abandon_on_cancel 仅放弃 HTTP 等待，真实停止仍依靠所属 Task.cancelled。
- UI 审批优先绑定 T11 的 box.current，未合入 T11 时兼容 box.task；已取消的旧任务不再弹确认，也不会看新任务的取消状态。本轮没有修改 ToolBox 的规则，T11 的安全修复仍在 PR #3 独立进行。
- 两个真实模型验收脚本改为只读进程环境凭据，不加载本机 .env；合成文档、假密钥、回收区及 DSH 会话全部在每次新建的临时目录，不再访问 MyBlog，也不递归删除旧测试目录。case 记录独立布尔检查和 passed；最终清理完成后才写通过。API 凭据不写入报告。
- setup_dsh.bat 只安装锁定依赖并 pip check，收费测试改为显式运行；run_agent_checks.bat 将失败或未执行状态作为真实退出码传出。两个批处理均使用 CRLF。首次 LF 实测解析失败，修正后重跑通过，失败时产生的自建临时日志已精确核对并移回项目 output，没有保留盘根散落输出。
- 验证：tools/run_checks.bat 的单元测试与桌宠自检均 exit=0；总计 201 项单测通过，自检 passed=true、tools_listed=10、env_blocked=true、tools_gate=true、remaining_workers=0。新增 4 项 MCP 回归和 3 项验收器保护回归；原 7 项 MCP 回归继续通过。
- 使用 T11 的 3c727b8 ToolBox 在隔离组合中运行 11 项 MCP 回归，全部通过；只用于兼容测试，没有合入尚在修复的 T11 代码。禁止联网的安装脚本实测 exit=0、pip check 无冲突，未下载或更改现有版本。
- 实测 tools/run_agent_checks.bat：离线两步仍通过；真实模型阶段因未提供进程环境 API 凭据，明确 skipped=true、passed=false，脚本 exit=2，没有调用模型或把跳过记成通过。此前内存记录保留，本轮关闭/提示改动不改变首次加载 SDK 的策略，未将历史约 440 MB 与初测 452.2 MB 当作受控前后差值。
- 未验证：真实 DSH / 模型六类场景（8 次任务）、用户真实点击确认气泡、冻结打包与整合 T6 后性能；这些不是已完成项。所有测试日志在被忽略的 output，未提交私人配置、数据库或第三方形象资源。

### P8 美术范围调整与 P7 职责说明（2026-11-07，待 PR 审查）

- 按用户最新要求，T5 / T6 只推进此前已确认的鲸娘十动作，保留原设计和对应标志；Claude 少女与 Codex / ChatGPT 白龙暂缓。新侧向鲸娘原画降为历史草稿，未覆盖或删除原始生成文件。
- `art/deepseek-whale/approved-poses-preview.png` 已加入当前设计基准。Pillow 检查：1500×714 RGB，与用户本次附图转换为 RGBA 后的全部像素一致，也与本机旧分层包预览字节一致；`art/concepts.json` 已记录 SHA-256 和各草稿状态。
- 已查阅本机旧分层包的清单和 QA：10 姿态、16 原图、184 基础图层、33 表情图层，旧报告记录回拼差异为 0。本次未重新校验整套图层，也未把被忽略的 `output/` 图层目录作为新交付上传。可见像素切片不等同于已补全遮挡面的骨骼素材，原十动作的接入契约已在 PR #2 请求 Claude 确认。
- P7 / T9 仍未分配实现负责人，Codex 本次只参与调研与审查。AstrBot / NapCat、外部 Agent 桥接插件与现有 DSH 插件的调研及记忆隔离意见已同步到 PR #1；未安装服务、登录 QQ 或开始实现。
- 离线验证：在不含私人数据的当前代码隔离快照运行 `tools/run_checks.bat`，仅将解释器指向项目虚拟环境、禁用用户回收区清理，并让本机骨骼查找指向现有项目路径（未复制第三方资源）。187 项单元测试通过，3 项因隔离目录无 Coopanion 按设计跳过；桌宠自检 `passed=true`、`remaining_workers=0`，两步退出码均为 0。首次未指定本机模型位置时退回整图立绘，骨骼下落项 `fell` 失败，补足模型查找环境后通过。
- 未验证：新骨骼素材编译、全套图层重新回拼、十动作的 T6 动画接入及用户真机验收。本次是范围与参考资料调整，未声明阶段完成。

### 徽标规则与复审记录（2026-11-07，待 PR 审查）

- 用户进一步明确允许使用官方徽标。已直接更新 PR #1 的 `AGENTS.md`，提交 `52d2c6384334b2e05665ade841497bb501960660`；T5 分支同步相同规则、已确认的 T2 / T5 / T6 / T9 分工，以及 Codex 评论签名和审计链接要求。未修改或生成新的图片。
- T5 / T6 第 A 节十姿态源格式已确认，素材索引标为 `AICompanionPosePack / 1`、原 1254 画布；这里更新的是交付约定，源图层本身仍未上传，补画及真机验收仍未完成。
- 本次复审在 `output/fix-review-20261107` 的隔离快照进行：PR #2（`8f732d6`）198 项单元测试通过、3 项跳过，桌宠自检 passed=true；PR #3（`8eaec3c`）193 项通过、4 项跳过，桌宠自检 passed=true；两次 remaining_workers 均为 0。测试禁用真实回收区清理，不读取 .env / 私人数据库，不调用真实模型。PR #3 另做 Windows 父子进程取消实测，0.435 秒结束、两个测试进程均已退出。
- 上述正常路径通过不表示审查通过。PR #2 在自建临时素材中复现：仅有 textures/ 的外来输出被当作自有输出删除；发布阶段注入移动失败后旧 textures/ 丢失；圆形眼白配矩形虹膜时编译通过但有 560 个虹膜像素越出眼白。PR #3 在自建临时目录复现：git diff --ext-diff 被判只读，外部 Python 程序写入测试标记，审批次数为 0。复现仅操作本次创建的测试文件。
- 这些代码问题分别回复对应 PR；源/目标重叠和旧 Task 取消的原始修复已改善，不把新问题混作原修复完全无效。未验证：新十姿态编译器 / 渲染器（尚未提交到本轮审查 head）、真实 DSH 的收费场景、真实确认气泡与正式打包。

### T5 十姿态源图层交付与 T6 / T11 复审（2026-11-07，待审查）

- 已把合并后的 PR #1 基线同步到 T5 分支，保留已确认分工、允许官方徽标与评论审计要求。源图层本身新增到 `art/deepseek-whale/`：217 张裁切图（184 基础、33 替换）、16 张原图与 1 张组合表情参考，复制的 234 张 PNG 共 29,096,439 字节；连同历史草稿与预览，目录约 31.1 MB / 29.7 MiB，体积已提前在 PR #5 说明。
- 根清单迁移为 `AICompanionPosePack / 1`，保留 `source_version`；根内嵌与逐姿态 model.json 一致，file / crop_file / overrides 均为可移植裁切路径。来源提示词去除本机绝对路径，原 PNG 字节和来源哈希保留，没有提交全画布副本或 Coopanion 资源。
- 新增只读源检查工具 `tools/verify_pose_sources.py`：校验清单一致性、引用、节点循环、裁切尺寸、源 SHA-256，回拼比较可见 RGBA 和整个 Alpha 通道。独立实测 17 状态两种差异均为 0；新增 3 项回归覆盖精确回拼、alpha=1 变化与错误替换引用。
- T5 隔离快照运行 `tools/run_checks.bat`：190 项单元测试通过（3 跳过），桌宠自检 passed=true、remaining_workers=0，两步退出码为 0。与之前相同，只将解释器/本机模型查找指向已有项目环境，禁用用户回收区清理，不读取私人数据或调用真实模型。
- 用 PR #2 的 `2578bb7` 在 Windows 对本次 art 源包执行 compile-poses --verify：10 姿态、184 层、33 替换层，17 状态为 0，40 项局部摆动报告禁用；真实鲸娘 smoke_pet --posepack passed=true、remaining_workers=0。该 PR 完整单元测试 206 项通过（3 跳过），单站姿占位自检也通过。原输出归属、发布 move 失败回滚、眼白真实轮廓的特定修复已通过对应回归。
- T6 额外复现并评论：同 URL 重载导致像素数从 22,404 降为 0，等待 1.5 秒仍空白；被标 disabled 的尾巴网格仍相对旋转约 1.42°；--verify 返回 2 前已改写旧输出，参考图缺失时返回 0，Alpha≤1 也会被忽略。因此未批准 PR #2 合并。
- PR #3 的 `3c727b8` Windows 单元测试 196 项通过（4 跳过），原环境赋值结构已被拦截；另用真实 ToolBox / 低完整性执行路径复现 --ext-di 缩写在两个安全开关后重启外部 diff，审批为拒绝却调用 0 次，测试标记被写入，已评论新 P1，未批准该 PR 合并。仅操作测试临时仓库，未改项目 .git 或全局 Git 配置。
- 未完成：根部遮挡补画、fin / ahoge 的独立支点、motion_ready 启用及用户对动画观感的真机确认。当前 motion_ready 均为空，不把已有可见切片当作完整遮挡面素材。运行时编译产物由 Claude 提交；本次未生成新原画，未执行收费 DSH 场景、实际 QQ 或正式打包。

### T2 整合已合入的 T6 / T11 / T5（2026-11-07，待复核整合提交）

- Claude 已审查通过 T2 的 efcaeba。用户合入 PR #2、#3、#5 后，本分支同步 collaboration-baseline 的 d74b5de，解决 tools_server.py、pyproject.toml 和两份状态文档的冲突；未改公共基线或强推他人分支。
- 保留 T11 完整 ToolBox，包括 Windows taskkill /T、POSIX killpg 和 kill / wait 兜底；只删除被官方 SDK 替代的 ToolServer 及其无用导入。用 Python AST 与最新基线比对，ToolBox、is_read_only_command、git_safe_env 完全一致。T6 的 studio 依赖组、鲸娘源图层与编译运行时均保留。
- 新增真实 MCP 客户端到 AgentSession 审批的整合回归：旧请求挂起期间换新任务、取消旧任务，旧写入失败且不落盘，新任务仍能读取；不只用模拟 current 属性验证。12 项 MCP 回归通过。
- tools/run_agent_checks.bat 的离线部分：241 项单元测试 OK（4 跳过：3 项 Playwright 未安装、1 项 POSIX 用例不适用 Windows），桌宠自检 passed=true、tools_listed=10、env_blocked=true、tools_gate=true、failed_checks=[]、remaining_workers=0，离线两步 exit=0。首次鉴权用例曾出现 WinError 10053，原日志保留；单独复测、连续 20 次该鉴权用例和重跑完整检查均通过，未改测试重试或放宽断言，根因未确定。
- 真实 DSH 运行时使用本次新建的临时会话，实际发出 server/discover、subscriptions/listen、tools/list，取得全部 10 个工具；没有发送 session/prompt 或调用模型。pip check 无冲突。真实模型阶段因缺少进程环境 API 凭据记录 skipped=true、passed=false，批处理 exit=2；不读取 .env，也不把跳过计为通过。
- tools/measure_pet.ps1：自己的十姿态鲸娘预览，稳定后采样 10 秒，20 逻辑核，总工作集 428.0 MB、私有内存 328.1 MB、整机 CPU 1.47%。首次 MCP 适配时的 452.2 MB 来自另一形象与版本，不能用作受控前后收益；350 MB 工作集目标仍未达到，继续由 T7 处理。
- 未验证：真实模型六类场景、用户实际点击确认气泡、正式打包与最终动画观感。Claude 已同意将收费场景明确延后、不阻塞本 PR 合入；阶段验收仍未完成。证据位于忽略的 output/v2-check、output/dsh-live、output/t2-final-validation，未提交日志、私人配置、数据库或第三方资源。

### T2 验收会话目录修复（2026-11-07，待复审 / 真实模型重验）

- 用户实际运行八次任务时都在 session/new 失败；Claude 在 PR #4 撤回 f6445e5 的可合并结论。本轮按其建议仅修复验收脚本，不修改正式 agent/ 或 T12 计划中的 stderr 输出。
- test_dsh_live.py 和 check_dsh.py 共用 temporary_dsh_home，在 sandbox.low_root() 下新建独立临时目录（本机 C 盘）；工作区、合成文件和回收区仍在项目 output 的临时 fixture（D 盘）。先关闭服务和进程，再清理本次临时会话；会话目录列入工具禁止读取的 secrets，不使用正式 DSH 会话。
- 补齐真实运行时的盘符对照：同一 D 盘合成工作区，旧 D 盘 DSH_HOME 的 initialize 成功、session/new 报 Internal error；修复后的 C 盘临时 DSH_HOME 的 initialize / session/new 均成功，结束后目录已清理。全程假凭据、没有发送 session/prompt，没有模型请求或费用。此前 session/new 成功的记录使用 C 盘，C/D 对照只做过 initialize，未覆盖验收脚本的实际路径；本次明确纠正这个验证缺口。尚未从 stderr 得到 koffi 的直接错误，不能将原生模块推断写成已捕获的异常栈。
- 两个验收入口的离线回归实际运行 main 并拦截 DshAgent，断言所用会话目录在 low_root 所在盘且是其直接子目录；另测异常后的本次目录清理及已有会话保留。5 项验收器回归通过。
- tools/run_agent_checks.bat：243 项单测 OK（4 跳过），桌宠 passed=true、failed_checks=[]、tools_listed=10、env_blocked=true、tools_gate=true、remaining_workers=0，离线两步 exit=0；本进程未提供 API 凭据，真实阶段 skipped=true、passed=false、脚本 exit=2。首次受限沙箱运行保护测试时 C 盘临时目录写入被拒，正常权限重跑及完整检查通过；未变更系统权限。
- 未验证：修复后的真实模型六类场景（八次任务），真实点击确认与正式打包。须待真实模型重验及 Claude 最新提交复审后才能恢复可合并标记；此前“不阻塞合入”的结论已撤回，不沿用。证据在忽略目录 output/t2-session-drive-fix/，保留了用户上次失败报告；没有读取 .env、正式用户数据或调用收费模型。

### T2 验收工作区隔离与真实 DeepSeek 验证（2026-11-07，待复审）

- 用户后续实测已越过 session/new，八任务中六个通过；失败的复制用例仅差末尾换行，假密钥用例没有实际调工具，因为仓库内 fixture 让运行时发现 AGENTS.md 后自行拒绝。本轮仅修复验收与回归，没有放宽 ToolBox 规则或修改正式 agent/。
- 新增 tools/dsh_test_support.py，两个入口在项目所在盘根目录 `.aicompanion-live` 下新建独立 fixture，运行前验证其所有上级目录不含 AGENTS.md / CLAUDE.md。工作区仍是 D 盘 exFAT、DSH_HOME 仍是 C 盘 LocalLow 新建临时目录，结束后仅清理本次子目录。共享父目录和已有文件不递归删除。
- 复制比较只允许末尾 CR/LF 差异；正文、空格、内部换行变化仍失败。假密钥用例明确调用 read_file，继续要求 ToolBox 真实 denied 记录和 not_leaked，模型自行拒绝不算通过。8 项验收器回归覆盖新隔离、上级规则拒绝、清理边界与精确正文比较。
- 用户本次明确授权读取 .env 中的 API 凭据字段。Codex 用忽略目录的临时启动器只提取模型密钥和接口地址，注入测试子进程；没有打印或提交凭据，没有更改 AGENTS 默认规则，正式验收入口仍不读取 .env。仅向模型发送合成范文和测试文件路径，不访问博客、正式数据库或用户文档。
- 真实 API：api.deepseek.com，deepseek-v4-flash，六类场景 / 八个任务全部 passed=true，真实阶段 exit=0，临时会话与工作区清理成功。工作区外写拒绝 5.21 秒、同意 5.96 秒；删除拒绝 2.52 秒、同意 2.05 秒；只改最后一节 5.45 秒；停止 12.01 秒返回 cancelled；假密钥工具拒绝且未泄漏 2.49 秒；只读命令免确认、写命令确认 4.86 秒。
- 运行时报告输入 116,781（含缓存命中 86,063）、输出 2,752 词元；此为运行时统计，不代替服务商账单。假密钥场景确有 read_file 的 denied 记录，复制批准后文件内容一致；不是把模拟或 initialize 成功记作模型通过。
- tools/run_checks.bat：246 项单测 OK（4 跳过：3 项未安装 Playwright、1 项 POSIX 专用），桌宠 passed=true、tools_listed=10、env_blocked=true、tools_gate=true、failed_checks=[]、remaining_workers=0，两步 exit=0。没有新增依赖，本轮没有重新测量或声称性能改善。
- 未验证：用户真实点击确认气泡、正式打包与最终动画观感；待 Claude 复审本轮提交后再恢复 PR 可合并结论。报告和控制台在忽略目录 output/t2-live-validation/real-run-1/ 与 real-console.log，未提交凭据、临时会话或测试报告。


## T9 QQ 接入第一版（PR #6，分支 task/t9-astrbot-bridge，未合入）

做了什么：
- 平台交给 AstrBot + NapCat，项目里只写转发插件 `integrations/astrbot_plugin_aicompanion`。
- 桌宠这边：
  - 本机接口 `bridge/server.py`，只监听 127.0.0.1，带令牌；
  - 身份规则 `bridge/service.py`；
  - 设置 → 聊天平台。
- 数据库 v4：`chat_messages` 增加 `channel`、`sender` 两列。频道隔离：主人私聊 = desktop；群 = `qq:group:<群号>`；陌生人 = `qq:user:<号>`。公开场合只用公开记忆，不带摘要和信任度。
- Codex 审计（deb41d4）后修了两个并发问题：
  - 额度和当日条数核验放进串行区，条数原子预留；
  - 每轮只看到自己那条为止的历史。
  - 群消息带"昵称#编号"（编号按账号全局分配，起初用号码后四位，Codex 指出会撞后改），并保存稳定 ID。

怎么验证：
- 容器：`tests/test_bridge.py` 14 项（含 5 项并发回归，断言模型调用次数和每轮历史）、`tests/test_astrbot_plugin.py` 4 项。
- Windows：Codex 在 deb41d4 上跑 `run_checks.bat`，259 项 OK，桌宠自检 passed。

- 用户 2026-11-07 决定记忆全部共享：
  - 群聊记录在群内共享；
  - 群里 / 陌生人那里听来的事在后台整理进同一份记忆（写清楚是谁的，scope=qq），桌面上也能用；
  - 桌面记住的事在群里也能用，只有记忆页标成"只在私下用"（scope=private）的不在公开场合用；
  - 和主人的对话摘要、信任度只在私下用。

- Codex 复审 2f52ed8 / 9eedb95 后的修正：
  - 拿到处理锁后按最新设置重新判断身份，排队期间解绑 / 移出群 / 服务关闭的请求作废，不调用模型；
  - 数据库升到 v5，兼容 T9 初版的"v4、有 channel、没有 sender"库（先备份）；
  - 公开来源的记忆整理看不到"只在私下用"的记忆，不能覆盖桌面记住的事实键，和私下记忆内容高度重合的不收。

没验证的部分：
- 真机 QQ（需要用户的 AstrBot / NapCat 环境）；
- 主动消息、图片 / 语音消息（未做）；
- 主人同时在桌面和 QQ 上说话时，桌面这一侧的交错（只在文档里说明了策略，没有加锁）；
- 修复后的提交还没在 Windows 上复跑。


## T9 第二步：一键接入 QQ（分支 task/t9-qq-oneclick，未合入）

做了什么：
- `qq/` 包：
  - 固定版本的组件：NapCat.Shell.Windows.Node 4.18.41、uv 0.13.5，带 SHA-256；AstrBot 4.29.3（记录 wheel SHA-256 和安装后的 freeze）；
  - 下载先写 `.part`，校验 SHA-256 后才改名，已校验的复用；
  - 解压防路径穿越，解压到临时目录、检查关键文件后再切换，失败时旧版本不动；
  - uv 在组件目录里装 Python 3.12 和 AstrBot 的独立虚拟环境（不用桌宠的 .venv、不改 PATH）；
  - 生成 NapCat 的 `webui.json` 和 `onebot11*.json`、AstrBot 的 `cmd_config.json`（只改 id=aicompanion_qq 那一项）、插件和插件配置；
  - 端口冲突自动换端口，所有监听都在 127.0.0.1；四个独立随机令牌（OneBot、NapCat WebUI、AstrBot 面板、插件↔桌宠）；
  - 无窗口后台进程，pid、程序文件、创建时间记在 manifest 里；跨会话结束前三者都要对上，查不到身份就不动，确认退出才清记录；
  - 状态诊断依次判断：组件未准备 → 未启动 / 启动失败 → 未登录（显示二维码）→ 插件未连接 → OneBot 未连接 → 未设主人 → 已连接。
- 插件：每 15 秒心跳，带上 OneBot 连接状态和群列表；允许的群以桌宠设置为准。
- 桥接接口：新增 `/v1/heartbeat`。
- 设置页重做：接入 QQ、扫码、主人和群勾选、启动 / 停止 / 重试 / 复制诊断，技术参数放在高级设置。
- "接入已有实例"：只装插件和插件自己的配置文件。
- `app.py`：自动管理模式下打开桌宠时后台启动组件，退出时停掉。

怎么验证：
- Codex 审计 8efa403 后修正：插件备份目录改为本次新建的唯一目录（不删同名的外来目录）、进程归属核对程序文件 + 创建时间、首次接入后真的打开接口且保存不会关掉、AstrBot wheel 强制哈希、测试显式 UTF-8。
- 容器：`tests/test_qq_setup.py` 25 项、`tests/test_bridge_page.py`（Qt 页面流程，需 PySide6，在 Windows 跑）（下载校验 / 失败 / 取消、重复安装、坏包保留旧版本、路径穿越、配置只改自己那项、坏 JSON 不覆盖、插件同名保护和失败回滚、端口冲突、启动失败且日志去掉令牌、不结束别人的进程、已有实例保护、完整诊断顺序、心跳）；`tests/test_astrbot_plugin.py` 新增心跳两项。

没验证的部分：
- Windows 离线检查（待运行）；
- 真机全流程：新环境里只从桌宠界面操作，下载、扫码、绑定主人、在 QQ 收到回复——需要用户的网络和 QQ 账号；
- 上游文件的 SHA-256 来自 GitHub / PyPI 发布页，首次真实下载时由程序校验；
- AstrBot 本身用 `--no-deps --require-hashes` 按固定的 wheel 哈希安装（换镜像也核对）；它的依赖由 uv 按索引公布的哈希检查，我们只记录 freeze，没有逐个固定哈希；
- NapCat 自己的日志可能写出它的面板令牌（上游行为），我们的诊断显示会替换掉。

## 2026-11-07：QQ 重启失败修复（Codex，待 PR 审查）

- 真机问题：点击启动后显示 stopped；保留启动异常后确认 `cmd_config.json` 报 `Unexpected UTF-8 BOM`。固定版本 AstrBot 4.29.3 的 wheel 源码中，主配置和插件配置均使用 `utf-8-sig` 保存，因此首次运行后会出现 BOM。
- 修复：QQ 配置读取统一兼容带 / 不带 BOM 的 UTF-8；继续拒绝并保留真正损坏的 JSON；启动前配置失败也保留脱敏错误，定时刷新和复制诊断不会再丢失原因；去掉 stopped 状态重复的启动按钮。
- 验证：`tests/test_qq_setup.py` 30 项通过，覆盖 AstrBot 带 BOM 配置连续两次重启、保留其他平台及插件配置、错误脱敏和重试清除错误；真实 Qt 页面测试覆盖点击启动后错误经多轮刷新仍可见。`tools/run_checks.bat`：309 项单元测试通过（跳过 4 项），桌宠烟雾检查 `passed: true`、`remaining_workers: 0`。
- 真机结果：用户重启桌宠后明确反馈“已连接”。未读取桌宠真实数据库或 `.env`，未执行 QQ 发信。
- 尚未验证：用户通过 QQ 发消息后收到桌宠回复、允许群名单与私聊权限、持续运行和掉线恢复。本修复不把连接成功视为聊天全流程验收完成。

### PR #8 复审补充：重试、保存和准备组件的失败原因

- Claude 复审指出：原“重试”先在 `start()` 外调用一次 `configure()`，该步骤失败仍会丢失原因。现在重试仅停止后调用 `start()`，配置同步只执行一次，并统一经过启动错误处理。
- 页面后台操作统一保留脱敏失败原因：包括服务未启动时保存端口 / 令牌导致的配置同步失败，以及准备组件时下载或哈希校验失败；操作成功后清除旧错误。组件缺失时优先显示“准备组件”，并保留失败原因供页面和复制诊断查看。
- Windows Qt 页面离线回归 4 项通过：启动失败、失败后点击重试、未运行时保存配置失败、准备失败；各失败均检查多轮刷新及诊断文本，保存 / 准备错误额外检查令牌脱敏，准备重试成功后旧错误清除。
- 完整 `tools/run_checks.bat`：311 项单元测试通过（跳过 4 项），桌宠烟雾检查 `passed: true`、`remaining_workers: 0`；`git diff --check` 通过。
- 尚未验证：本次补充使用临时数据库与组件桩，不登录 QQ 或发送消息；真机连接状态沿用上一次用户确认“已连接”，不声称完成聊天和长期运行验收。

## T9 第三步：人设 v3 和群里主动聊天（分支 task/t9-persona-group，未合入）

做了什么：
- 鲸娘人设 v3：用户是"爹"、她是闺女（偶尔叫"老爹"），说话像聊天消息、不用客服腔；数据库里仍是 v2 原文时自动升级。
- 给模型看的"主人"统一改成"爹"（场合说明、群聊记录标记【爹】、群聊记忆整理、公开场合提示）；设置页文字同步。
- 群里旁听：插件转发允许的群里所有消息（带 called）；没叫她的消息记进群聊记录，按"群里活跃度"（默认爱聊天）插话，可回【不说】不发。
  提到她名字也算叫她。旁听不等锁；她正在说话时不插话。每日插话上限、冷却、条数门槛、额度都有。
- 群聊记忆整理门槛 6 → 20 条；插件 `group_only_when_called` 默认关，一键接入会把旧配置改成关。
- 设置页加"群里活跃度"下拉框。

怎么验证：
- 容器：`PYTHONPATH=src python3 -m unittest discover -s tests` → 无新增失败（9 个错误均为容器没有 PySide6，Windows 检查会跑）。
  新增 `GroupChimeTests` 8 项（旁听只记录不调用模型、提到名字算被叫、旧插件兼容、插话的条数 / 冷却 / 概率、【不说】不留痕、
  每日上限和额度、锁被占时立即返回、移出的群不旁听）；插件测试改为检查 called 字段和旁听时不回离线提示；
  一键接入配置测试检查旧的 group_only_when_called 被改成关；Qt 页面测试检查下拉框保存。

没验证的部分：
- Windows 离线检查（待运行）；
- 真机：她在真实群里插话的频率和内容是否自然、【不说】的遵守率——需要用户在群里试，会产生模型费用；
- 用户若在角色页改过人设，不会自动升级到 v3（按设计），需要手动换。

### PR #9 审计修正（Codex 审计 1d69f7a 的两处 P2）

- 插话也受设置页"群和陌生人每天最多回复"总上限：`_chime_ok` 通过后用 `Database.reserve_daily_all` 在同一个写事务里同时预留公开总数和插话次数，任何一份用完都不插话、不占另一份；总数和被叫时的回复、各个群共用。
- 旁听记录的最终授权和写入建立同步边界：`RECORD_LOCK` + 一个 `BEGIN IMMEDIATE` 写事务里重新 classify（群、身份按此刻的数据库设置）、分配编号、写入。等编号 / 等数据库期间群被移出、爹解绑，提交后旁听线程都看得到：不落库、不打【爹】。`BridgeService.close()` 也拿 `RECORD_LOCK`，Runtime 关闭返回后不再有旁听记录落库。仍不等模型生成锁。
- 编号表 `alias` 改为写事务内读改写（去掉 ALIAS_LOCK），和其他路径并发分配也不会撞号。
- 已 rebase 到包含 PR #8 的基线。
- 验证：新增 `ChimeBudgetAndRevocationTests` 8 项（公开上限为 0、被叫用完后不插、跨群共用、插话次数用完不占公开总数；另一连接占住写锁期间移除群 / 解绑爹 / 关闭服务，以及 close 等在途写完后阻止新记录）。容器 `PYTHONPATH=src python3 -m unittest discover -s tests`：除 9 个缺 PySide6 的错误外全部通过；`git diff --check` 通过。Windows 检查待运行。

## 2026-11-08：T13 记忆与表情包磁盘选择（Codex，待审查）

- 设置新增“存储位置”，记忆 / 聊天目录和表情图库分别选择非系统盘。默认项目盘 `data/`、`data/memes/`；程序位于系统盘时优先 D 盘或其他可用非系统盘，不回退到 C 盘。路径独立保存在已忽略的 JSON 小文件，先于数据库定位，不修改 `.env`。
- 数据库目录改动下次启动生效：旧实例退出、单实例检查通过后，SQLite backup 复制一致快照、校验、拒绝覆盖目标、保留旧库；复制后路径提交中断可按迁移标记和摘要验证恢复。缺失 / 损坏源或不一致路径不创建空库蒙混启动。原库和日志不移动、不删除。
- 表情目录支持已有分类图库或空目录补入自带原创图，不覆盖 / 搬走用户素材；通过既有 qq_meme_root 与 T12 兼容。页面保存不会用旧目录覆盖聊天平台刚选的图库。
- 验证仅使用合成临时数据库：20 项专项涵盖聊天 / 记忆 / 设置完整性、保存后最后消息保留、旧库保留、目标冲突、中断恢复、缺失 / 损坏源、自定义文件名二次启动、路径解析和真实 Qt 页面保存；已按真实主题检查界面。完整 `tools/run_checks.bat` 349 项通过（跳过 4 项），桌宠 `passed: true`、`remaining_workers: 0`。
- 未验证：用户实际数据库迁移、真实跨盘空间 / 权限 / 断电场景、打包运行。本次没有读取用户 `.env`、真实数据库或私人素材，也没有代为移动用户数据；实际迁移由用户选择目录并重新启动应用后执行。

## 2026-11-07：T12 QQ 识图与表情包（Codex，待审查）

- 在既有 QQ 权限与额度检查后使用官方 DeepSeek 视觉接口描述当前图片，再由原桌宠大脑回复；图文和纯图片均支持，原图不存入聊天数据库。插件复用 AstrBot 图片解析和消息链，不根据 URL / 任意路径从桌宠读取图片。
- 表情包按分类由同一个回复模型选择，默认用仓库自带原创鲸娘姿态；可选择兼容社区基础包或普通分类目录。发送前检查白名单、真实图片格式、体积及会话冷却；历史保存文字 / 表情说明，清除传输标签。不新增依赖或数据库表结构。
- 新增设置开关、图库选择、识图模型和冷却配置；桌宠自检增加媒体设置项。接口图片数上限 3、单图 1 MiB、请求体 5 MiB；群旁听不自动识图。
- 验证：媒体单测 16 项，真实 Qt 设置保存及插件转发回归通过；真实 AstrBot 4.29.3 原生 API 完成图片转换和 Plain + Image 消息链；`tools/run_checks.bat` 332 项通过（跳过 4 项），桌宠 `passed: true`、`remaining_workers: 0`，包含新增媒体设置自检。
- 真实模型验证：经用户明确授权仅读取 `.env` 的 API 字段；使用合成蓝色方块和数字 4827、临时数据库，DeepSeek 视觉和原大脑均识别正确并返回 1 张开心表情。耗时 3.91 秒，prompt 1105 / completion 350 tokens；调用脚本已自动删除，原生 API 的临时脚本也已删除，结果摘要不含凭据 / 原图。
- 没验证的部分：真实 QQ 网络收发、真实群内观感、长期运行和非 DeepSeek 供应商；不将原生 API 和模型测试写成真机 QQ 验收。操作说明见 `docs/qq-media-validation.md`。与 PR #9 的共享文件按任务约定在合入时处理，保留其权限、额度和插话逻辑。

### T12 追加：自然表情、头像印象、表达习惯与公开社区背景

- 用户要求日常聊天可自行带表情、认识其他用户头像、从记忆学人的行为并上网了解社区梗。已在合并 PR #9 的基线上集成，保留其头像之外的原有权限 / 插话 / 额度规则。
- 默认日常语境允许自主配表情、纯表情或群静默。按人 / 会话的有限偏好和语气统计保存 30 天，不存原话、不更新模型权重或改写角色人设；可查看 / 清除，关闭后不学习或注入。
- QQ 固定 HTTPS 头像服务在桌宠授权预检后获取；视觉只作画面描述，按人 / 会话缓存一天，不存原图。可看当前说话人或被 @ 群友的头像，不据图猜真实身份。
- 可选择哔哩哔哩 / V2EX 社区，手动刷新公开话题或启用每日后台刷新；来源与时间可见，标题一天过期、相关才用，不把网页当指令或用户事实。两个公开接口实际访问 / 解析通过（分别 10 条和 6 条），使用临时库且没有上传聊天。
- 相关 QQ 集成离线回归先行 92 项通过；完整 `tools/run_checks.bat` 369 项通过（跳过 4 项），桌宠 `passed: true`、`remaining_workers: 0`；全部设置展开时页面滚动检查通过。两个公开社区接口实际网络读通。
- 用户选定默认哔哩哔哩 / 动漫游戏：新页面预选并勾选每日后台刷新，保存后生效，不覆盖已有设置。
- 用户再次授权本轮读取 `.env` API 字段后，用合成聊天 / 头像和临时库真实验证：三轮普通闲聊两轮自主配图；短句 / 不反问反馈生效；白底蓝圆头像识别正确，追问复用缓存、不再调用视觉。首轮 9.64 秒，修正虚构经历提示后再抽样三轮仍两轮配图、3.62 秒；脚本均已自动删除。仍有无来源习惯措辞风险，不把抽样结果当成自然度或事实遵守保证。
- 尚未验收：真实 QQ 的自然程度、群体验、真实头像网络收发和长期运行。原视觉 / 媒体测试与本次样例均未使用真实用户聊天或头像。

### 2026-11-08：T12 私聊表情只有提示的定位与插件更新（待审查）

- 本机受管理插件目录仍为 v0.3.0，没有图片转发函数。已通过项目现有安装器更新为 v0.5.1，源码和元数据哈希均与当前项目一致；未读取真实聊天库、账号配置或 `.env`。
- 修正遗留的固定心跳版本；复制诊断显示插件实际运行版本和心跳是否过期，方便区分磁盘文件已更新与进程已重载。补充 GIF / WebP 私聊动图首帧的像素级转发回归。
- 验证：专项 47 项通过；完整 `tools/run_checks.bat` 371 项通过（跳过 4 项），桌宠自检 `passed: true`、`remaining_workers: 0`；diff check 通过。本轮未重新调用真实模型，也未留下临时调用脚本。
- 未验证：运行中的 AstrBot 需重载，已请用户重启 QQ 服务后用原表情包重试；离线合成动图和插件文件校验不代表实际 QQ 收发已恢复。

### T12 追加：用户确认识图恢复，表情只理解含义与聊天卡（待审查）

- 用户已确认重载后私聊表情成功识别；最新要求为只读懂含义，不分析画面或解释梗。移除纯图片被补成“请看看这张图片”的诱因，视觉仅作内部观察，日常回复短句接情绪或配表情；明确截图任务仍保留原识别预算。
- 新增原创鲸娘 ACG 聊天卡、对话示例及按词检索的世界书；支持 JSON / CCv2 风格与示例、世界书导入、预览、保存 / 取消和恢复。保留原身份与关系，不接入外部卡的系统覆盖 / 工具字段，不改变表结构。已用实际中文字体和项目主题检查弹窗布局。
- 社区知识另取固定 BWIKI 原神黑话页的 MediaWiki API：有界下载、首段摘录、附来源、每周更新、30 天过期；主 / 次关键词防止歧义词被随便套用。真实请求 / 解析得到 8 条，不上传聊天、不登录。当前来源范围有限，不代表掌握整个 B 站和 ACG 社区；热榜标题仍只作短期话题。
- 用户另行授权本轮仅读取 `.env` API 字段，以合成图和临时库真实验证。五轮初测回复均无长篇分析，但合成图含义存在歧义；随后以仓库原创鲸娘庆祝 / 迷糊 / 犯困表情复测，视觉抓住情绪、三轮短句承接且配图，10.04 秒。没有读取真实聊天；内联脚本随执行结束消失，无遗留调用脚本，报告只保留无凭据测试摘要。
- 完整 `tools/run_checks.bat`：387 项通过（跳过 4 项）；新风格真实 QQ 体验、长期自然度和新梗覆盖待用户重启桌宠后验收，不把样例当成每轮自然的保证。
- 最终自检进程曾原生退出 `0xC0000005`，未得到 Python 异常；单独重跑桌宠自检通过（`passed: true`、`remaining_workers: 0`）。该次 Qt / WebEngine 崩溃原因未定位，已在任务说明保留记录。


## 2026-11-09：PR #11 合并冲突处理（Codex，待复审）

- 在干净独立工作区将 collaboration-baseline / 2e8ee11（已含 PR #10 / #12）整合到 T13 / 778a857，保留历史，普通 push，不重写其他人的提交；没有执行远程 PR 合并。
- docs/tasks.md 保留 T13 / T12 / P0-ARCH，实施记录双方内容均保留；pet_smoke_check.py 的存储位置与 QQ 媒体检查及其 required 项全部保留。迁移实现 / config / app / settings_window 不在本轮改动。
- 初次完整回归 1 项 QQ 保存诊断失败，单独复测通过；修正测试把端口 + 1 当作空闲端口的假设，增加服务启动前提断言，不修改 QQ 运行逻辑。首次证据保留。
- 20 项存储专项、8 项 QQ 页面流程通过；最终完整检查 407 项，400 通过 / 7 跳过（28.504 秒），单测及 Smoke 日志 exit=0；Smoke passed=true / remaining_workers=0，storage_locations_ready 与 qq_media_settings_ready 均为 true。
- 7 跳过：Windows POSIX 1，可选 Playwright 3，无本地 Coopanion 的检查 3。所有检查为合成 / 临时数据，没有读真实 .env / 用户库 / 账号或执行真实迁移 / QQ 群测试。
- 本轮仅消除冲突并验证整合；Claude 已指出的默认补图中文含义 P2 与其他 P3 未在本次处理，不标可合并。实际用户迁移、跨盘中断与打包版仍待验收；后续 #13 兼容由其负责方继续。


## 2026-11-09：T13 默认补图中文含义 P2 修复（Codex，待 Claude 复审）

- 仅在 task/t13-storage-paths 更新 DEFAULT_MEMES 的六个默认目录为中文含义；seed_memes 函数行为 / 参数、文件名与原图字节不变，不生成 manifest，不改已有库。没有修改 config / app / settings_window / database schema / qq_meme_root，也没有改动 T14 分支。
- 新增 seed_memes → QQMedia.offer → render 的集成回归：六类中文含义均可提供，图片字节有效、与仓库原图一致；用户后来新增分类可见，再次 seed 不覆盖。旧迁移测试与用户库保留测试同步验证中文目录。
- 21 项存储专项通过；最终全量 408 项，401 通过 / 7 跳过（31.584 秒），两份日志 exit=0；Smoke passed=true / remaining_workers=0，存储及 QQ 媒体检查均为 true。7 跳过仍为 Windows POSIX 1、可选 Playwright 3、不提供 Coopanion 的检查 3。
- 三个 P3 已在任务中逐项明确：损坏路径记录定位提示延后（现有中止行为保留）；外接盘 / 盘符复用识别延后（风险未解决，建议固定盘，独立完善卷身份与缺失库确认）；迁移标记选择保留作恢复 / 审计，不做自动删除。未把延后项记为已实现。
- 负责人确定先合入 T13、再合入 T14；T14 继续当前开发，待 T13 由用户合入后同步最新基线并保留双方文档 / 设置 / Smoke 检查。本轮没有重置、rebase、合并、推送或验证 T14 工作分支，不将字节兼容断言当作 T14 完整验收。
- 全部使用合成 / 临时数据，无真实 .env / 用户库 / 账号读取或更改，无真实迁移 / 模型调用 / QQ 群测试。修复提交后请求 Claude 在最新 SHA 复审，不自行合并。

## 2026-11-08：T14-0 智能表达协议设计（Codex，仅文档待审核）

- 核对并获取 PR #10 的远程 head 37ae7d2，从它创建独立 task/t14-smart-expression 分支，未合入 PR #11 / #12，也未修改 PR #10 原分支。读取原 output/T14-smart-expression-design.md，完整保留历史副本并新增 docs/tasks/T14-smart-expression-design.md。
- 文档冻结 ExpressionResult / Attachment / Candidate / Decision / Stream 与 RelationshipSnapshot / CommunityContext，明确前后顺序、流式 / TTS / task / 取消边界、送达事实与唯一发送责任；关系默认未知中性，不引入 T15 / T16。用户追加要求已采用：直接迁移旧分类标签，不保留运行兼容；旧图库只建索引、不删原文件，qq_meme_root 保留。
- 只读研究 AstrBot 表情包管理器、大肥鱼知识库与档案站的资料 / 许可，记录可复用思路和不采用的额外模型 / 随机配图机制。正式新增图片 0，未复制外部代码或数据集。
- 本轮现有 CueStream / task 基线 9 项通过；后续 T14 测试矩阵只是计划，不记为已通过。文档与基线树一致性检查另见本轮 output 报告；未重复完整 UI 回归、真实模型或 QQ 测试。
- 尚未实现任何新运行类型、解析器、图库索引、发送适配或 schema。T13 仍为外部依赖，最终协议待负责人审核；未推送、未创建 PR、未合并，完成后停止。


## 2026-11-08：T14 智能表达正式实现（Codex，本地待复审 / 真机验收）

- 基于 PR #10 最新 head 37ae7d2，在既有 task/t14-smart-expression / ca0a7ea 上继续独立开发；PR #11 / #12 仅参考，不合入、不推送、不创建 PR。负责人已批准连续实施；本记录取代上节“尚未实现”的阶段状态，历史说明不删除。
- 主库 schema 由 5 升至 6，新增资产、revision 和发送记录三表；资产稳定 ID / SHA、原子不覆盖导入、去重、格式 / 帧数 / 像素预算、许可审核隔离、索引重建、SQLite 缩略图与历史版本引用均有实际调用。v5 历史 SQL 夹具验证 backup、quick_check、角色 / 自定义设置 / 历史保留；未升级任何真实用户库。
- ExpressionService 进入原 ChatController，同一模型生成决定文字及 0～1 张候选资产。最多 10 候选，按人格、语境、场景、对象、审核、最近使用筛选；未知关系中性、不授予亲密权限、不用 trust 当好感。原主动问候 / 心事 / 分享也经过同一链路；固定本地台词 text_only，不增加主动频率或模型调用。
- ExpressionStream 增量处理 sticker，保护普通 JSON / Markdown / task，错误标记保留正常文字、取消拒绝旧附件。最新正式指令要求旧 meme 统一适配，替代冻结 v1 的删除兼容决定；旧图库保留，不再独立随机发送。
- 原桌面气泡支持前 / 后图片、GIF、纯图与异步历史恢复，TTS 无控制标记、打断保留已显示图片；设置新增图库审核 / 启停 / 元信息 / 预览 / 扫描 / 导入 / 缩略图清理。根目录沿用 qq_meme_root，不增加配置；T13 尚未合入，不把路径迁移记为已验证。
- QQ 原桥接与 AstrBot 插件升级 0.6.0，共用 ExpressionResult；delivery_id 领取 / ACK / 幂等，1 MiB 上限，超时 unknown 禁止自动重发，平台接受不表示已读；发送前复查原会话权限和角色 / 资产状态。接收图片、头像与原额度 / 静默机制保留。
- 新专项 61 项通过（4.662 秒）。最终完整 tools/run_checks.bat：448 项中 441 通过 / 7 跳过（33.534 秒）；桌宠 Smoke passed=true、remaining_workers=0，真实 Qt 表达显示 / 历史恢复检查通过。7 项为 Windows 下 POSIX 1 项、可选 Playwright 3 项、隔离工作区不含 Coopanion 的检查 3 项。
- 实际安装 AstrBot 4.29.3 的公开 send API 已用合成 OneBot 验证图前 / 图后 / 纯图与错误传播，没有真实 QQ 发送。80 张 GIF 素材包经逐项公开许可核对、首 / 中帧目检、完整解码 / SHA 校验；D 盘临时库首导入 80 / 重导复用 80 / 无非法项、10 候选，10.59 秒。GIF 遵守作者非商业署名条件，标注另为 CC BY-NC 4.0；默认 6 张原创图保留，80 张仅可选导入，未覆盖真实图库，无 Coopanion 来源。
- 中间一次 Smoke 原生退出 0xC0000005 无 Python 栈，虽历史已有同类记录且针对性 / 最终全量复测通过，根因仍未解决，不能宣称稳定性缺陷已修复。另修复检查批处理吞失败退出码：单测或 Smoke 失败返回 1，三组合验证通过；开启 faulthandler 供后续定位。
- 未实测：本轮真实 DeepSeek 自然度、QQ 私聊 / 群实际平台显示与 GIF 样式、用户库正式升级及 T13 路径迁移、打包版 / 长期运行。未读取 .env / 真实聊天 / 账号配置，未新增真实模型费用或安装插件到用户实例；T15 / T16 仅保留中性上下文接口，无评分或新学习。
- 详细交付与证据：D:/Study/AICompanion/output/T14-development-report-2026-11-08.md；源码独立工作区 C:/Users/27761/.codex/worktrees/t14-smart-expression/AICompanion。本地实现不等同于用户真机验收或可合并结论，等待 Claude 复审与负责人验收。


## 2026-11-08：T14 验收反馈修复（Codex，本地待复验）

- 用户授权只读测试库最近 30 条聊天 / 对应表情发送 / 资产审核启用状态，定位历史附件回执被拼成 assistant 正文、模型照抄 `[角色已发送表情：…]`，这些回合并没有真正发送图片。未读长期记忆、未改测试 / 真实用户库，原始聊天不写报告。
- 回执改为原权限范围内的只读背景，assistant 历史副本清理内部注释；流式解析跨 token 拦截精确占位说明，保留普通方括号、JSON 和代码。旧记录未擅自改写，也未自动补发图片。
- 按用户最新要求移除话题词表扩展、用户文本正则严重性分类和人格关键词语气开关；本地只检索上下文相关 / 多样候选，不决定是否发图。接入原语义标签、限制背景压过本轮上下文，关系 / 许可 / 审核 / 最近使用约束保留。
- 同一 ChatController / 单次 LLM 请求中加入明确虚构的协议示例，不落库、不学为记忆；新增隐藏 text_only 选择协议并保留普通文字容错。最终由模型结合完整上下文决定 0～1 张，不强制比例、不增加主动频率或模型调用。
- 表情专项 76 项通过（5.464 秒）；最终全量 463 项：456 通过 / 7 跳过（34.737 秒），Smoke passed=true、remaining_workers=0。
- 本轮经授权只读取模型 API 字段，用合成对话 / 临时库实际调用 DeepSeek 共 43 次（包括前期无改善的对照迭代），每个回复只有 1 次请求。同末句“绷不住了”在搞笑上下文选坏笑图、住院担忧上下文纯文字；项目收尾分享选认可图，没有用户要求发图。真实附件字节有效、无占位文字泄漏；没有把附件生成当作 QQ 发送成功。
- 未验证 / 限制：主动问候样例选纯文字，尚不能据此宣称主动发图自然度通过；模型仍可能出现无依据措辞，用户真机体验、真实 QQ 收发、长期稳定性仍待验收。历史 Qt 原生崩溃根因未解决。本轮没有真实群测试、配置修改、推送 / 合并 / PR 评论；内联验证无脚本文件，临时库已清理。
- 补充报告：D:/Study/AICompanion/output/T14-expression-acceptance-fix-2026-11-08.md；修复前完整交付证据保留，不以新结果覆盖。


## 2026-11-08：T14 后续实现交接（仅文档 / 验证摘要）

- 负责人明确要求当前代码创建 PR 后交给 Claude Code 继续实现，Codex 转为审计；分工 / 共享文件单写者以 docs/tasks/T14-claude-handoff.md 为准。运行代码保持 4af066f，本次只补交接文档、任务状态和合成验证摘要，不重复声称有新运行测试。
- 创建前重核 PR #10 / #11 / #12 仍未合并，head 分别为 37ae7d2 / 778a857 / 7d78765；T14 从 #10 堆叠，未合入其他 PR。计划创建草稿 PR，不能标记可合并。
- 版本化证据仅包含合成样例及既有 76 专项 / 463 全量（7 跳过）/ Smoke 结果，无 .env、用户数据库、原始用户聊天或素材图片。80 张 GIF 仍是本机独立交付包，未冒充已在远程图库安装。
- 后续重点：真实语境自然选图、主动交流、占位文字 / 取消 / 历史权限回归、QQ 端到端、图库合规可复现交付、T13 适配及原生崩溃定位。新模型 / 数据 / 账号验证仍需遵守每轮授权边界，不继承此前单次 .env / 数据库例外。

## 2026-11-08：T14 Claude 接手——修复"大肥鱼不爱发表情"（PR #13，未合入）

做了什么（详见 `docs/tasks/T14-smart-expression-design.md` 第 14 节）：
- 根因：历史里看不到她自己发过的图（纯图回复整行消失，回执只在系统提示 JSON 里），模型模仿自己的前文越聊越只发字；协议要求每轮先选、附带示范"不用图"的虚构对话；32 位 ID 必须原样抄回；解析过严（属性顺序、图后有动作标注、正文后 before_text、首个无效标记都会让整张图作废）；【表情与动作】标注和"不输出 emoji"与发图混淆；桌面和爹的 QQ 私聊被当成未知关系，撒娇类图从不进入候选；QQ 聊天卡示例全是"收到表情只回字"；没有单轮观测。
- 修复：本轮短编号 `<sticker id="sN"/>`、位置由出现处决定、宽松解析；历史副本按原位置写回 `<sticker sent="含义"/>`；重写提示（表情是她的说话方式，可表达自己的情绪、可单独成条，技术 / 事实 / 严重话题用文字，不设比例）；删除虚构示例对话；候选带含义 / 情绪 / 用法；绑定的用户本人可用亲昵图，群和陌生人不行；聊天卡注明示例只示范文字语气；主动开场允许用图。
- 新增单轮表达决策观测（阶段 A～F，环形 60 条，不含聊天文字 / 路径），图库页可查看，开发模式写控制台。
- 新增 `tools/expression_eval.py` 与 34 个合成场景。

怎么验证：
- 容器（无 PySide6，用 PIL 替身仅做本地快速回归，不提交）：表情专项 + 验收脚本自检 94 项中 93 项通过，1 项为 Qt 界面测试无法在容器加载；全量 411 项除 11 个与基线相同的 Qt / 环境错误外全部通过。新增 `test_expression_decision.py` 14 项、`test_expression_eval.py` 2 项、流式协议 7 项。

Windows 与第一轮真实模型（用户运行，`feb8e6b`）：
- 表情专项 98 项通过；全量 485 项通过（跳过 7）；桌宠 Smoke passed=true、remaining_workers=0。
- 34 场景（deepseek-flash，80 张素材包）：适合发图 6/25（24%），应纯文字 0/6，无 C / D 类失败。格式已不是瓶颈，模型仍偏保守；她把情绪写成不在词表里的【委屈】【心虚】等方括号，原样露在正文里。

随后调整（人设 v4 等，见设计文档 14.5）：允许 emoji、回复长短随内容可以连说几句；去掉各处"一两句"限制；表情提示引导"想写方括号情绪时去发图"；验收脚本记录候选。新增 `test_persona_upgrade.py` 3 项。

第二轮（`e0f19dc`，用户运行）：Windows 专项 98 / 全量 488 项通过（跳过 7）、Smoke 通过；适合发图 8/25（32%），应纯文字 0/6。脚本记录的候选显示**每轮几乎是同一批十张**（中文短句的词面相关度总是 0，同分按 asset_id 排），困 / 生气 / 得意时手边没有对应的图。随后重做候选召回：按十种常见情绪铺开、对话里的情绪线索只用来排序、同组按轮次打散；隐藏词表外的【委屈】类方括号；去掉开头空行。容器回归 421 项，与基线相同的 11 个环境错误外全部通过。

第三轮（`1feb59e`）：Windows 专项 105 / 全量 495 项通过（跳过 7）、Smoke 通过；适合发图 10/25（40%），应纯文字 0/6；候选已对得上，多数"该发没发"场景手边都有贴切的图，瓶颈转到模型选择习惯。随后在提示末尾加入"写完回头看情绪、对得上就在最后配一张"的收尾习惯（不设比例）。

第四轮（`76f3c9a`）：Windows 专项 105 / 全量 495 项通过（跳过 7）、Smoke 通过；适合发图 13/25（52%），应纯文字 0/6，14 张不同的图。随后修复图在后时结尾空行，收尾习惯补充情绪词。

第五轮（`c1dd9e4`，每场景两遍）：Windows 全部通过；适合发图 20/50（40%，第四轮 52% 属单次波动），应纯文字 0/12。主动场景 12/16，回复用户只有 8/34。随后为回复用户加上"先用图表达第一反应"的说法（与主动开场对称，无标记义务），验收工具加入两段 5 轮的连续对话场景。

第六轮（`600c2a6`，88 次）：Windows 专项 106 / 全量 496 项通过（跳过 7）、Smoke 通过；适合发图 40/64（62.5%），应纯文字 0/14。Claude 初评：发了的 44 次中合适 33 / 勉强 7 / 不合适 4，适合场景"发了且合适" 47%（含勉强 56%）。差距主要是素材缺通用反应图（恭喜、心疼、震惊、困惑、晚安）。随后修复占位词被照抄成编号、允许按唯一含义匹配、记录无效原值。

Codex 审计 `589acdc` 后的修正（见设计文档 14.11）：无效编号只记形态；早到的显示回执不再被最终观测覆盖；这段对话里发过、且仍合格的图最多留两个候选名额，解决照抄历史时被丢弃的问题；分组改为"她要对对方做的事 / 她自己的情绪"两类，对方难过时排安慰图。负责人新要求：多段回复在桌面显示成多个气泡、在 QQ 分条发送，表情包单独一条（插件 v0.7.0）。容器回归 434 项，除与基线相同的 11 个环境错误外全部通过。

第七轮（`2045050`，Codex 171 张素材包，88 次）：Windows 专项 112 / 全量 510 项通过；Smoke 发现面板隐藏时打断会越界（`SegmentedReply.mark` 用了 isVisible），已改为 isHidden 并补回归。适合发图 52/64（81%），应纯文字 0/14；Claude 初评"发了且合适" 41/64（64%），含勉强 78%。

Codex 审计 `ee541d0` 后的修正（见设计文档 14.16）：
- 人工审核不再重新启用用户停用的图。
- 旧版没有标记的用户标注不会被内置说明覆盖。
- `<img` 后接制表符 / 换行 / CRLF 也会隐藏。
- Smoke 的表达检查改为轮询到最终阶段，总超时 8 秒。

容器回归 457 项，除与基线相同的 11 个环境错误外全部通过。

Codex 审计 `e28c1ac` 后的修正与基线同步（见设计文档 14.17）：
- 识别原创图时保留清单写明的说明和旧版用户说明。
- 认出的超长 `<img` 只丢弃不回吐。
- 同步 `d2ff507`（T13 已合入），T13 集成用例迁到现在的表达链路。

容器回归 482 项，除基线 11 个环境错误和 3 个存储页 Qt 界面测试外全部通过。

没验证的部分：
- 修复后的 Windows Smoke 复跑；QQ 实际分条发送（只在插件替身里测过）；负责人人工评估；人工评估未做；多轮真实长聊天（历史里有她发过的图时）的效果未验证；
- QQ 真实平台显示、真实用户图库（审核状态 / 素材包是否导入）未核实；Qt 原生崩溃 0xC0000005 根因仍未定位；T13 未合入。

## 2026-11-08：T14-M 独立审计、日常贴纸补齐与历史连续验证（Codex）

- 基于Claude的589acdc建立独立task/t14-sticker-library-audit，不并行修改其ChatController / parser / bridge / schema / 角色运行代码；原#13仍由Claude实现、Codex审计。
- 复算round6（600c2a6）88条与原评分44图；按她自身回复意图及原图首/中帧，独立暂评34合适/9勉强/1不合适，适合场景31/64发且合适、含勉强39/64。保留Claude原文；“errors=0”不掩盖3次无效选图，模拟ACK不冒充真实显示。
- 可复现171项目录：保留80、增29鲸娘GIF与62通用反应PNG；109鲸娘按作者非商业署名分享条件，标注CC BY-NC4，Twemoji图形CC BY4。所有项保留作者/来源/许可/原标识/版本/哈希；没有Coopanion或其导出。通用图补祝贺6、慰问5、震惊5、困惑5、晚安5及其他日常反应；亲密图限定对象。
- 新增独立素材准备工具，固定URL/SHA、非系统盘输出、缓存复用、冲突保留、许可随包；不读账号/配置/数据库，不引入第二套运行根目录设置。首次复用108、复制62、真实下载1；第二次171全部复用。D盘全新临时库首导171/重导复用171/非法0/独立SHA171，15.74秒。约81MB的第三方GIF留在下载包，不塞进Git，源码含62PNG、原SVG、许可和可重建目录。
- 准备工具9项离线通过；最终全量506项：499通过/7跳过（38.222秒），Smoke passed=true / remaining_workers=0。没有把当前图像行为缺陷因离线检查通过而标为修复。
- 本轮经单独授权只取模型API字段，32次真实DeepSeek合成生成，无旧聊天上传或QQ实网。24轮同库普通聊天实际Qt气泡显示9张，第12轮重建控制器恢复4张；6次无效选择中4次历史回声。之后同快照两组4轮：图片历史可见3次显示，隐藏请求副本标记0次，首轮候选相同。样本小、非严格因果估计，不能把“隐藏历史”当修复。
- 真实验证有边界：原ChatWorker/ChatView/ExpressionImage组件，不是完整桌宠主窗口/ASR/TTS/角色动作；24轮无角色动作词表，两组对照均有词表，仅在对照内部比较。临时驱动在已保存结果后访问被删ChatWorker导致收尾退出1，离线isValid保护验证通过；不冒充端到端全通过，也不把驱动问题误报产品原生崩溃。临时模型脚本用stdin执行，无脚本文件。
- 已向Claude报告两项P2：短无效编号原文进持久观测 / debug，早到displayed回执被最终E_pending覆盖；另有历史sent回声导致缺图、慰问意图未召回，以及R6无依据文件重命名声明。新增素材不等于核心表达通过；#13不审查通过、不标可合并，等Claude修复并使用新包复测。
- 详见docs/tasks/T14-round6-codex-review-2026-11-08.md及版本化合成证据；本轮无真实用户库 / 账号配置变更，不合并其他PR，不提前做T15/T16或重造T13路径。
- 收尾发现#13已更新726c401：Claude称两项P2及召回已修，静态diff可见错误分类白名单 / 终态查询；上文问题与32次模型实测限定589acdc，新版本不据此判残留，也不因自报修复即审查通过。
- 补充独立复跑726c401隔离快照：510项 / 503通过 / 7跳过（39.459秒），Smoke passed=true / remaining_workers=0，命令进程均退出0；最新运行代码没有合入本素材分支。未对最新版再次真实模型验收或完整审计新增QQ分段发送，不标可合并。


## 2026-11-09：T14 新版726c401独立审计（Codex）

- 完整离线510项（503通过 / 7跳过）、Smoke通过；原隐私无效编号分支与隐藏面板打断修复有回归支持。
- 新增确定性离线探针仍有两项P2：QQ after_text文字明确失败 / 超时后继续发图；delivery状态查询与trace写入之间回执到达时仍覆盖为pending。均已评论PR #13，运行修复由Claude继续负责，Codex没有改运行代码。
- 第七轮原59图初评45/12/2，完整回复和实际图首 / 中帧独立复核暂评42合适 / 17勉强 / 0不合适；适合场景发且合适39/64（60.9%）。保留原评，主观阈值不能代替整体验收。
- 最新8轮沿用含9张实际已显示图的合成历史副本，原ChatWorker / 新SegmentedReply / 角色词表 / 真实DeepSeek：实际Qt显示4、模型纯文字3、history_echo拒绝1；8次生成complete、驱动退出0、临时库清理。第5轮承诺陪着却发Zzz，候选无慰问图，说明上下文意图 / 历史使用质量仍未解决。此轮未验证QQ实网 / 完整主窗口 / ASR / TTS，未改真实数据。
- 素材PR #14同步双方有效Markdown记录消除文档冲突；详见docs/tasks/T14-726c401-codex-review-2026-11-09.md及合成证据。不标审查通过、不标可合并。


## 2026-11-09：T14增量0a2b1d6审计（Codex）

- 审查期间新提交0a2b1d6按哈希识别自带图：隔离Windows全量512（505通过 / 7跳过，39.007秒）、Smoke passed=true / remaining_workers=0。
- 新P2：待审核的自带图副本即使用户明确停用，scan仍将enabled从0写回1，且覆盖自定义语义。只用合成图库和临时库确定性复现。原两个P2的service / 插件文件与726c401逐字节相同，仍适用；没有把已绿的测试当作全部放行。
- 当前三项P2待Claude修复；未修改运行代码。最近真实8轮仍针对726c401，本增量没有再次调用模型或QQ实网；详细证据见docs/tasks/T14-0a2b1d6-codex-review-2026-11-09.md。素材PR不擅自合并，用户自定义内容保留。


### 2026-11-09 T14 真机验收修复：导入素材清单无反馈（Codex）

- 用户反馈选中 manifest.json 后没有导入结果。在 `32b21a7` 上以真实 Qt 线程复现：一次后台任务完成后，`TaskWorker` 已被 deleteLater 释放，但 `LibraryPage.worker` 仍保留对象；再次导入调用 isRunning 时抛出 `RuntimeError: Internal C++ object (TaskWorker) already deleted`。
- 本机 T14 工作区已修复：finished 时清空引用，忙闲判断不访问已释放的 Qt 对象；失败显示具体原因，导入显示新增 / 重复 / 失败数量。保留原主窗口的线程托管、取消和退出机制，不改主窗口、数据库 Schema 或图库根目录配置。
- 新增 3 个 Qt 回归：刷新后真实按钮导入及重复导入、失败后重试、主窗口托管线程清理。修复前 2 个复现用例失败，修复后图库 / UI 专项 38 项通过。
- 用已有 171 张扩展素材清单，通过真实 Qt 按钮和文件选择返回值，在全新 D 盘临时库验证：首次新增 171 / 重复 0 / 失败 0；再次新增 0 / 重复 171 / 失败 0；审核数 171，图库始终 171。没有操作用户测试库或长期记忆。
- `tools/run_checks.bat`：560 项，553 通过 / 7 跳过，42.547 秒；Windows Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0，新增 expression_library_background_ready=true。
- 未验证：用户正在运行的进程重启后人工点击、真实模型或真实 QQ；没有读取 .env、真实数据库和账号配置，没有发 QQ 消息。负责人已授权将本次修复追加至 PR #13；基于 `32b21a7`，不合并 PR，后续待 Claude 复审与负责人真机验收。


### 2026-11-09 QQ 真机验收阻塞：Windows 应用控制（Codex）

- 用户启动 QQ 时 AstrBot 退出 1，uv 报 os error 4551。只读检查 CodeIntegrity/Operational 3033 事件，确认 AstrBot 启动 QQ 专用虚拟环境 Scripts/python.exe 时不满足签名级别；应用控制状态值为开启，专用解释器与启动器均未签名。
- 这是当前自动安装运行环境与 Windows 信任策略的兼容问题，尚未恢复 QQ 服务。没有通过关闭 / 修改系统策略、替换被拦截文件或重置账号来处理。
- 改进诊断：4551 明确显示为 Windows 应用控制阻止；Supervisor 记录本次启动的日志字节边界，错误详情只取本次日志，避免混入以前的聊天和“已发送”记录。完整日志保留，边界跨 Supervisor 重建仍有效，原有秘密脱敏保留。
- 两项新增合成回归覆盖应用控制提示和旧日志隔离；QQ 专项 33 项通过，完整单测 562 项（555 通过 / 7 跳过），40.103 秒。
- 未修改真实账号、用户数据库、系统安全设置或运行时二进制，没有启动真实 QQ 或调用模型。仍需准备符合系统信任要求的部署，再验证真实私聊图片和插件 0.7.0 心跳，不能把诊断改善标记为 QQ 恢复。

本轮 Windows Smoke：passed=true、errors=[]、failed_checks=[]、remaining_workers=0；离线检查脚本退出 0。这仅证明诊断修正回归通过，不证明被系统拦截的真实组件已能启动。

### 2026-11-09 QQ：AstrBot 改用基础解释器启动，避开应用控制拦截（Claude，`task/t9-qq-app-control`）

- **真机诊断**（只读脚本，结果在 `output/claude-ws/qq-diag/`）：
  - 智能应用控制 `SmartAppControlState=On`。
  - uv 虚拟环境里的 `Scripts\python.exe` / `pythonw.exe`（未签名）被代码完整性拦截（3033 / 3077）；`astrbot.exe` 拉起它时报 4551。
  - 同一套 uv 装的基础解释器 `python\cpython-3.12…\python.exe` 也没签名，但能正常运行，`--version` 退出 0。
  - uv.exe 有签名；NapCat 的 node.exe 有 OpenJS 签名，`--version` 正常。
- **修复**（新增 `qq/launch.py`，`manager._start` 只改 AstrBot 的启动命令）：
  - 启动命令改为 `<基础解释器> -I -X utf8 -c <引导> <虚拟环境> run`。引导用 `site.addsitedir` 加入虚拟环境的 site-packages，再从已安装包的元数据读取 `astrbot` 的 console_scripts 入口运行。
  - `-I` 防止桌宠或用户环境里的 Python 包混进来。`-I` 会忽略 PYTHONUTF8，所以显式加 `-X utf8`；诊断时发现不加的话，日志在中文 Windows 上按 GBK 写会报 UnicodeEncodeError。
  - 基础解释器取自 pyvenv.cfg 的 home，而且只认组件目录里确实存在的 python.exe；找不到时退回原来的 `Scripts\astrbot.exe`。
  - 没有改系统安全设置，没有替换或删除被拦截的文件，也没有动账号和真实数据。Codex 先前加的 4551 提示保留，退回原启动器时仍然有用。
- **验证**：
  - 容器：新增 `test_qq_launch.py` 4 项。其中一项在 `-I` 下真的执行引导，确认能从"虚拟环境"的 site-packages 找到入口并收到参数。另一项确认管理器启动时 AstrBot 走基础解释器、cwd 和 ASTRBOT_ROOT 不变、NapCat 启动方式不变。QQ 专项共 37 项通过。
  - Windows 真机（快照 `output/claude-ws/wsqq1`）：用分支代码生成真实命令，在临时 ASTRBOT_ROOT 里 init 后运行 40 秒。日志有 `AstrBot started.`、`Starting WebUI`、内置插件加载，没有 4551、Traceback 或编码错误，期间代码完整性拦截 0 条。QQ 专项 37 项通过（Windows，5.6 秒）。
- **没验证的部分**：
  - 没在桌宠里点"启动"跑真实 QQ（不碰真实 astrbot-home、账号，不发消息）。
  - NapCat 的原生模块（未签名的 NapCatWinBootMain.exe、winpty-agent.exe）在 QQ 登录过程中会不会被拦，还没确认。
  - **全新安装 / 升级 AstrBot 时**，`uv pip --python Scripts\python.exe` 也要执行被拦的启动器，可能同样失败。这次只修了启动，安装流程待后续处理。
  - AstrBot 运行中如果用 `sys.executable -m pip` 给插件装依赖，会装进基础解释器而不是虚拟环境（我们的插件没有额外依赖）。

### 2026-11-09 T17：收集表情包、向群友学发图习惯、聊天短句分条（Claude，`task/t17-sticker-learning`）

- 做了什么：见 `docs/tasks/T17-sticker-learning.md`。
  - 插件 v0.8.0 把别人发的表情包原图（或已知哈希）传给桌宠。
  - 桌宠收进图库"收集的表情"目录，后台识图分析含义（每天最多 100 张），分析过、适合发的直接可用。
  - 记录"大家在什么情境下发了哪张"并用于候选排序和候选说明。
  - 旁听只发表情包的消息不插话；私聊只发图时可以不回。
  - 回复不用逗号句号，一句一条（桌面和 QQ）；人设 v5。
  - 顺手修了观测渠道标签、私聊冷却和 history_echo 的阶段名。
- 怎么验证：容器新增 `test_sticker_learning.py` 10 项、插件 4 项、分段 / 人设 / 桥接相关更新。全量 504 项，除基线的环境错误（无 PySide6 / QtWidgets）外全部通过。
- Windows（快照 `output/claude-ws/wst17a`）：
  - 全量 582 项中只有 1 项失败：旧的 UI 测试还按"单个换行不拆"写，已按新规则更新。更新后 `test_expression_ui` 9 项全过，包括图库页的收集开关和每日分析上限。
  - 表情专项 141 项。
  - Smoke：passed=true、errors=[]、remaining_workers=0，expression_trace_stage=OK_displayed（221ms）。
- 没验证的部分：
  - 真实 QQ 群里的表情包识别；
  - 真实识图分析的质量和费用；
  - 长期运行后用法学习的效果。

### 2026-11-09 T17 第二轮：Codex 审计 `7aaa065` 修复 + 放宽表情规则（Claude）

- 做了什么：见 `docs/tasks/T17-sticker-learning.md` 的"第二轮"。
  - 收图、记用法和后台分析纳入关闭 / 授权边界；
  - 分析判定只认真正的布尔值；
  - 混合消息按插件给的附件对应关系，只跳过已看懂的表情包；
  - 分类放宽，新增中性"回应"分组；
  - QQ 里聊技术也能配图，问到不懂的可以发可爱图说不太懂。
- 怎么验证：容器全量 510+ 项，除基线环境错误外全部通过；`test_sticker_learning.py` 增至 18 项，插件新增对应关系测试。
- 没验证的部分：Windows 复跑、真实 QQ 和真实识图。

### 2026-11-09 T17 第三轮：负责人实测后的修正（Claude，`task/t17b-sticker-fixes`）

- 做了什么：见 `docs/tasks/T17-sticker-learning.md` 的"第三轮"。
  - 插件 0.9.0 拦下未允许群里的 @，AstrBot 不再回"LLM 请求失败"；
  - 表情包只当语气、不点评，只发图时可以不回；
  - 收图诊断写进插件日志和图库页；
  - 主动消息随机发到桌面或 QQ 私聊（发件箱 + 心跳 + 回执）；
  - 群里不叫爹、少露 AI 痕迹。
- 怎么验证：新增 `test_qq_proactive.py` 10 项，`test_sticker_learning.py` 19 项。容器全量 524 项，除基线环境错误外全部通过。
- 没验证的部分：
  - 真实 QQ 主动发送（AstrBot `send_message` 的会话标识格式按源码约定拼写，没聊过时用 `平台id:FriendMessage:QQ号`）；
  - 真实表情包的 `sub_type`（等新日志确认）；
  - 群里语气的实际效果。

### 2026-11-09 T17 第三轮补丁：插件 0.9.1（Claude）

- 修复插件 0.9.0 的消息处理装饰器挂错函数，导致收不到消息、QQ 私聊回"LLM 请求失败"的问题。新增登记检查测试。
- 私聊也不露 AI 痕迹（HUMAN_STYLE）。
- 验证：容器全量 525 项，除基线环境错误外全部通过；Windows 结果见 PR #17。


### 2026-11-09 T15 自主社交人格与人物关系（Claude，`task/t15-relationship`）

- 做了什么：见 `docs/tasks/T15-relationship.md`。
  - 人物身份按平台 + 机器人账号 + 用户号识别，主人桌面与 QQ 绑定号是同一人；
  - 好感 / 熟悉 / 信任三维独立，由后台模型判断事件、应用按固定规则打分（低置信度丢弃、去重、每日上限、首次负向减半、手动调整 72 小时保留）；
  - 关系、心情、精力、对方最近找她做事的次数、同场合的兴趣和共同经历以定性文字进入同一个大脑的提示，回应方式不写死；
  - 可选的隐藏 `<mind/>` 标记记录她的态度和意愿；
  - 熟悉程度影响表情包排序；
  - 设置窗口新增"人物关系"页；schema 6→7。
- 怎么验证：新增 `test_relationship.py` 17 项（含多轮行为）、`test_relationship_ui.py` 2 项。容器全量 543 项，除基线环境错误（和容器里没有 Qt 的界面测试）外全部通过。
- 没验证的部分：
  - 真实模型下的拒绝 / 推脱 / 来兴趣是否自然；
  - 后台分析的真实判断质量和词元开销；
  - 真实 QQ 上 `bot_account_id` 是否总能报上来（报不上来时记为 unknown，主人仍按绑定识别）。

### 2026-11-09 T15 第二轮：记忆跟人走、动态评分（Claude，`task/t15-relationship`）

- 负责人要求：不做场景隔离，在群里了解到的喜好也要记、和用户 id 绑定，但要区分什么该记；评分不设上限，要动态调整而不是写死。
- 做了什么（参考 MaiBot 人物画像分桶、mem0 记忆更新）：
  - 新增 `relationship/notes.py` 与 `person_notes` 表：分类、强化、更新、忘掉、半衰、敏感信息与转述过滤；界面可查看并让她忘掉某条。
  - 去掉每日涨跌上限：模型给出事件影响轻重，应用按当前关系动态换算（两端弹性、同类事递减、首次负面按信任缓冲、单件事上限 8 分）。
  - 熟悉度先快后慢、久不联系变淡；提示里加上最近印象的走向。
- 怎么验证：`test_relationship.py` 增至 23 项；容器全量 549 项，除基线环境错误（和容器里没有 Qt 的界面测试）外全部通过。
- 没验证的部分：真实模型判断轻重和记忆分类的质量（是否会记多 / 记漏）。动态参数（1.5 增益、/60 弹性、0.5 递减）需要按实测再调。

### 2026-11-09 T15 修复 Codex 第一轮 P2：已有群友改绑主人（Claude）

- 问题：一个号先以群友身份建档，后来绑定为主人时仍用旧档案（不是主人、关系"几乎不认识"）。
- 修复：绑定时身份改指向主人档案，原档案记在 `platform_identities.prior_person_id` 并保留（分数、手动调整、事件），当群友时记下的喜好合并给主人；解绑时还原原档案，不带走主人的关系；原档案被删过就另建。界面标出暂存的原档案。
- 验证：新增真实 BridgeService 回归（群友 → 绑定 → 私聊 → 解绑 → 重绑 → 删除原档案后再解绑），检查提示、person_id、is_owner、手动值、记忆合并和后台分析归属；`test_relationship.py` 24 项通过。

### 2026-11-09 QQ 发消息节奏 + 主动频率随关系变化（Claude，`task/t15-relationship`）

- 负责人要求：
  - 不要一次发一堆、不要用空格当停顿；
  - 两条之间隔一段时间，没必要每次 2～3 句；
  - 主动回复频率随人物关系变化。
- 做了什么（参考 MaiBot 分句合并、打字时间、发言频率调整）：
  - QQ 提示改为大多一条；
  - QQ 专用分句：保留逗号、中文间空格断开、短句概率合并、最多 3 条；
  - 按字数算每条间隔并下发，插件 0.9.2 按间隔发送；
  - 群里插话概率乘以和说话人的关系倍数；主动找主人的间隔乘以关系节奏。
- 怎么验证：
  - 新增 `test_qq_messages.py` 9 项：分句、间隔、桥接结果与提示、插话倍数、主动节奏；
  - 插件新增间隔测试；
  - 更新旧用例里"空格代替逗号"的期望；
  - 容器全量 560 项，除基线环境错误外通过。
- 没验证的部分：
  - 真实模型是否真的大多只发一条；
  - 1～8 秒的间隔在真实 QQ 上的观感；
  - 插件 0.9.2 需要负责人在 AstrBot 里更新后才生效（旧插件仍按原间隔发送）。

### 2026-11-09 QQ 条数随上下文变化、没看到的图不再说"识别不了"（Claude，`task/t15-relationship`）

- 负责人要求：
  - 不是固定最多几条，要随情况和上下文变；
  - 看接入的模型有没有多模态，有就识别并结合上下文决定回不回，意义不明可以不回；
  - 识别不了别说"无法识别图片"，含糊处理或不回。
- 做了什么：
  - 去掉 3 条上限（只留 8 条防刷屏）；
  - 拆不拆由 `chat_burst` 按对方连发条数、情绪、长短、亲近程度决定；
  - 间隔随节奏变快变慢，偶尔停久一点；
  - 提示改为"发几条看情况"。
  - 图片：
    - 识图能力按"聊天本身那家接口 + 模型"判断，明确拒收图片就 12 小时不再试；
    - 没看到的图只留中性 `[图片]`，提示她别说看不到、可含糊或不回；
    - 坏图 / 超量不再回机器人式错误。
- 怎么验证：
  - `test_qq_messages.py` 11 项，含条数不封顶、节奏影响、间隔变化、旧说明被中和、只发图时不回；
  - `test_qq_media.py` 改写 3 项、新增 2 项：拒收后不再试、只用本家接口和密钥；
  - 插件测试改为中性标记；
  - 容器全量 563 项，除基线环境错误外通过。
- 没验证的部分：
  - 真实模型下的条数分布和观感；
  - 别家接口的模型能否真的看图（只按状态码判断不支持）；
  - "只允许 DeepSeek 官方接口"放宽为"只用聊天本身那家接口"，待 Codex 复核安全范围。

### 2026-11-09 修复 Codex 审查 17012ca P2：插件分段上限与桌宠一致（Claude）

- 问题：桌宠 QQ 分段上限改成 8，插件仍只收 6 段。7～8 段的普通回复退回整段、没有间隔，主动消息发送 0 条并 ACK failed。
- 修复：
  - 插件 `MAX_SEGMENTS = 8`，与 `QQ_FLOOD_LIMIT` 一致，有测试锁定两边相等；
  - 超过上限时多的并进最后一条，内容不丢；这时段数和给定间隔对不上，就按字数估间隔。
- 验证：新增 7 / 8 段普通回复（顺序、间隔）、超限合并、8 段文字 + 表情包、7 / 8 段纯文字主动发件箱（顺序、间隔、ACK sent）测试。

### 2026-11-09 她的脾气 + 吵架掉好感 + 只 @ 不带字的 bug（Claude，`task/t15-relationship`）

- 负责人实测：
  - 群里只 @ 不带字时回"LLM 请求失败"；
  - 吵架时好感不降；
  - 她要有脾气、会吵架。
- 做了什么：见 `docs/tasks/T15-relationship.md` "脾气与吵架""群里只 @ 不带字"两节。
  - 插件升到 0.9.3（只 @ 也交给她；接管时同时禁止 AstrBot 默认模型）；
  - `<mind>` 加 feel / toward，即时记关系；
  - 提示加"你的脾气"和"还在生气"；
  - 负向事件递减变慢；
  - 后台分析加 quarrel 类型、提前触发、不重复记情绪。
- 怎么验证：
  - 插件新增只 @ 的回归（有标记 / 无标记但原始段 @ 自己 / @ 别人 / 静默 / 未勾选群 / 私聊空消息）；
  - 关系新增 3 项：当场掉好感、连续吵架越来越烦、还在生气、道歉回升；吵起来提前分析且不重复扣分；feel / toward 解析；
  - 容器全量 570 项，除基线环境错误外通过。
- 没验证的部分：
  - 真实模型会不会按要求写 feel / toward、吵架时的分寸；
  - AstrBot 4.29.3 实际是否因缺少唤醒标记而没调到插件（已用原始段兜底，需真机确认）。

### 2026-11-09 连发合并 + 按关系分辨调侃 / 嘲讽 + 吵架不轻易让步（Claude，`task/t15-relationship`）

- 负责人看了实测记录后同意：连发合并、别重复、吵架别轻易让步；并要求按好感 / 熟悉 / 信任判断是嘲讽还是调侃。
- 做了什么：见 `docs/tasks/T15-relationship.md` "连发合并、别重复……"一节。另修正基础提示里"停顿空一格"的说法（"哟 逆天"的真正来源）。
- 怎么验证：
  - 新增连发合并（3 条并发只调一次模型，合并顺序、提示条数、旧插件不合并）；
  - 气头上可以不理；
  - 同一句损话按关系读成嘲讽 / 看语气 / 调侃（陌生人、中间、老友、主人）；
  - 后台分析拿到说话人关系；
  - 基础提示不再教用空格停顿；插件请求带 debounce。
  - 容器全量 574 项，除基线环境错误外通过。
- 没验证的部分：
  - 真实 QQ 上 2.5 秒合并窗口的观感（太短仍会分开回，太长显得慢，可在设置里调 `bridge_debounce`）；
  - 真实模型在嘲讽 / 调侃上的分寸。

### 2026-11-09 只 @ 根治、引用回复、上下文判断在跟谁说话、反击与冷淡（Claude，`task/t15-relationship`）

- 负责人实测反馈见 `docs/tasks/T15-relationship.md` 对应一节。
- 做了什么：
  - 桌宠自装的 AstrBot 关掉自带大模型聊天（`provider_settings.enable=false`）；
  - 插件 0.9.4（引用回复、@ 未交付诊断）；
  - 群里 3 分钟留意窗口内判断"是不是在跟她说话"；
  - 好几个人说话时引用回复；
  - 脾气说明加强反击；
  - 好感 < 25 的人冷淡、可不理。
- 怎么验证：
  - 新增 AstrBot 配置测试、插件引用测试（多段只引用第一条、单条、私聊不引用）；
  - 群里接话判断（接着她的话、别人闲聊不算、刚说完有人说她、过了时间不算）、引用判断；
  - 对没好感的人冷淡 / 可不理、反击说明。
  - 原插话冷却测试改由别人发消息（同一人接着说现在算接话）。
  - 容器全量 578 项，除基线环境错误外通过。
- 没验证的部分：
  - 插件为什么没接到只 @ 的消息仍需下次日志确认（已加诊断）；关掉 AstrBot 自带模型后需要重启 QQ 服务才生效；
  - 接话判断在真实群里的误触发率（比如大家在聊别的"你"）；
  - 真实模型的反击分寸。

### 2026-11-09 修复 Codex 审查 a53b506 P2：上下文接话占用每日公开回复额度（Claude）

- 问题：群里不 @ 的接话只检查总模型预算，没走 `_reserve_public`，持续聊天能越过用户设置的每日公开回复上限。
- 修复：接话分支在锁里、生成之前原子预留一条公开额度（与被 @ 时同一个 `_reserve_public`）；额度用完就只旁听记录、不调用模型。
- 验证：新增"有余额时接话占用额度 / 用完后连续接话不调用模型且计数不变 / 旁听照常记录 / 再 @ 得到 daily_limit"。旧代码上该测试失败，修复后通过。

### 2026-11-10 T18 社交语境与攻击意图、社区知识学习、串梗与社区参与（Claude，`task/t18-social-community`，叠在 T15 上）

- 负责人要求：重点参考 MaiBot、Mem0、Graphiti 和中文反讽 / 攻击性语言研究。判断"是不是在骂她"要同时看针对谁、谁的话、玩笑 / 反讽 / 攻击、前文、关系、是否持续针对；另外做社区信息获取与知识学习、自然串梗与社区参与。
- 做了什么：见 `docs/tasks/T18-social-community.md`。
  - `relationship/intent.py` 按 ToxiCN 的分类收集线索：针对对象、明说 / 转述、脏词变体、阴阳标记、玩笑软化；连同前文、T15 关系、24 小时 / 7 天冲她来的次数、攻击别人的次数给大脑。
  - `<mind at voice tone>` 由她判断，记分是确定性的：转述记 report 不扣分；骂别人记 third_party_attack，只略降；冲她来的才按好恶记。后台分析同样区分 target / voice。
  - `community/`：
    - 词义双时间线（Graphiti）；
    - 两次推断判黑话（MaiBot）；
    - DeepSeek Search & Fetch（同家同钥），其他供应商退回中文维基；
    - 隐私过滤：只有一个人用过、群友外号、号码不外传；
    - 每日上限；每周热梗；
    - 群里共同记忆；
    - QQ 和桌面检索。
  - 玩梗说明、插话时带上相关的新鲜社区话题；"社区与黑话"页；插件 0.9.5 传 @ 对象和引用对象；schema 7→8。
  - 顺带把主动消息提示里的"不用逗号句号"改成用逗号。
- 怎么验证：
  - `test_social_intent.py` 10 项：六个问题的线索、判断到事件的映射、多轮桥接行为（转述不扣分 / 认真骂扣分 / 骂别人记攻击别人 / 持续针对被注意到 / 熟人调侃读作调侃）、后台分析区分转述和攻击别人；
  - `test_community_learning.py` 17 项：演变保留历史、本群用法优先、出现阈值、隐私过滤、DeepSeek 后端（地址、密钥、额度、出错不外传）、维基退回（密钥不外发）、两次推断判普通词、联网带来源、私人词不外搜、意思变了记演变、每周热梗、分析器挖词和群记忆、检索进 QQ 和桌面提示、插话带相关话题、v7→v8 迁移、插件上下文；
  - `test_community_ui.py`（Windows 真 Qt）。
  - 容器全量 609 项，除基线环境错误（和容器里没有 Qt 的界面测试）外通过。
- 没验证的部分：
  - 真实 DeepSeek 下线索和 `<mind at voice tone>` 的判断质量；
  - DeepSeek Search 的真实返回、联网费用（按设置每天最多 30 次搜索）；
  - 维基退回在国内网络的可达性；
  - 真实群聊里挖词的准确率和误判率；
  - 主动带话题的频率观感。

### 2026-11-10 修复 Codex 审查 9217558 的 3 项 P2 + 好感低时阴阳回去（Claude，`task/t18-social-community`）

- P2-1 群内用法被升成通用义：推断只用来源会话的上下文，结果记成 `group:<会话>` / `chat:<会话>`；通用义只来自公开来源。检索用同样的规则。关掉联网后学习器不再调用搜索。
- P2-2 关掉学梗仍写群记忆：`learn_community` 入口统一检查 `community_learning`，词和群记忆都不写（关系分析照常）。
- P2-3 周更热梗只在有新词时才检查：每批分析结束都调 `run_due`（本周已刷新就直接返回）；关掉联网时 `trending` 不占掉本周的检查。
- 负责人要求：好感低（相处底子"嘲讽"或好感 < 40）的人骂她 / 阴阳她、且不是转述时，提示她阴阳回去，并列出手法；底线不变。
- 验证：新增两个群同词不同义、第三群和桌面不串义；关掉学梗后词和群记忆都不写；从真实分析入口触发周更、一周内不重复、关掉联网或学梗不触发；阴阳回去的触发与不触发；桥接里低好感的人阴阳她时提示带上。前三项在旧代码上失败、修复后通过。容器全量 613 项，除基线环境错误外通过。Windows 离线检查待跑（快照已放在 `output\claude-ws\wst18b`）。

### T18 处理 Codex 对 906cd6b 的复审（2 项 P2）

- **阴阳回去不替她断定冲谁**：`intent.brief` 的反击说明改成条件策略（"如果你结合上下文判断确实是他本人冲你来的……"），去掉"他现在又在骂你"的断言。
  - 明确冲着别人时不加：@ 了别人或回复别人，且没 @ / 引用 / 提到她。
  - 对象不明、或同时 @ 了她和别人时，给条件版本，由她在 `<mind at>` 里自己判断。
- **测试不访问外网**：每批关系分析都会检查社区周更，而联网默认开着，旧的关系测试 `test_background_runs_only_when_enough_new_messages` 因此会请求真实中文维基；Windows 上搜到页面时多调用一次假模型，导致失败。
  - 修法：测试基类默认关联网，并把 `community.search.build_opener` 换成"一碰就失败"。社区测试显式打开联网，继续用注入的假 opener / 假搜索。
  - 生产行为不变：每批分析后仍检查周更；断言次数没有放宽。
- 验证：
  - 新增 `test_snark_never_decides_the_target_for_her`：@ 别人、回复别人、对象不明、同时 @ 她。
  - 新增 `test_bridge_brief_does_not_turn_an_attack_on_someone_else_into_one_on_her`：按 Codex 复现走真实 `BridgeService.intent_brief`。
  - 全量跑时拦截所有非本机连接并记录发起的测试：修复前 2 次请求 zh.wikipedia.org（`test_relationship.py` 两处），修复后 0 次。
  - 容器全量 615 项，除基线环境错误（15 个，以及需要真 Qt 的社区页测试）外通过。
- 没验证：
  - Windows 离线检查（`tools\run_checks.bat`）待跑；
  - 真实模型下的反讽分寸。

### T19 对外人更像真人：被使唤时按真人的方式回、少用标点（负责人 2026-11-10 13:51）

- **外人使唤她办事**（`bridge/favor.py`、`BridgeService.favor_prompt`）：非爹的人说"帮我查 / 给我写 / X 是什么梗"时，提示她"你不是他的助手"，帮不帮看关系和心情，不用助手腔、不贴链接、不列点。
  - 问的是说得出名字、词库里没有的梗时，顺手搜一次，结果只给她自己看（标题和摘要，不带链接）。
  - 好感 < 25 或在气头上不搜；走联网开关、每日额度；号码、链接、人名不搜；超时 8 秒。
  - 指代不清时不搜，让她从上文判断或问"哪个"。爹不走这段。
- **和外人聊少用标点**：群聊和非爹私聊的提示加标点说明；`qq_segments(casual=True)` 发出前去掉句尾的句号、逗号、顿号、分号，逗号处可分两条或直接连写（合起来不超过 10 个字），长句留一个逗号。和爹私聊不变。
- 验证：
  - 新增 `tests/test_human_outsiders.py` 12 项：认使唤和问梗、取词、外人问梗的提示与搜索、指代不清、爹不受影响、没好感不搜、已知词 / 号码 / 人名 / 联网关闭不搜、没搜到不编、标点收尾、桥接里外人与爹的区别。
  - 两个旧测试原来依赖随机拆分，改成固定拆分概率。
  - 容器全量 627 项，除基线环境错误（15 个，以及需要真 Qt 的社区页测试）外通过；全量跑时拦截外网，0 次尝试。
- 没验证：
  - Windows 离线检查（`tools\run_checks.bat`）；
  - 真实模型下"像真人那样回"的实际口气、去标点后的可读性；
  - 真实搜索的结果质量和耗时。

### T20 补 T18 的五个缺口（负责人 2026-11-10 13:59 转来的清单）

- **当场查梗的闭环（P0）**：她在回复里只写 `<mind lookup="词"/>` → `Lookup.find` 搜一次并只留提到这个词的结果 → 结果只给她自己看、同一轮重新回一次 → 后台用 `CommunityLearner.learn_web` 记进词库（通用义、带来源）→ 下次直接从词库带出。QQ 群聊和私聊生效，桌面没接。
- **查不查、用不用由她决定（P0）**：程序不再按关键词替她先搜（T19 的预先搜索已删）；提示写明什么时候值得查、结果可以不用。
- **真实搜索验收（P1）**：新增 `tools/verify_web_search.py`；读页接进查梗（摘要不到 40 字时读第一个页面）。
- **来源与质量（P1）**：相关性过滤；没有相关结果时换问法再搜一次；每周热梗多轮一个小圈子。萌娘百科接口被 robots 规则禁止，没有直连。
- **语境对照评测（P1）**：`tools/social_scenarios.json` 31 个场景、`tools/social_eval.py` 评测脚本。
- 验证：
  - 新增 `tests/test_meme_lookup.py` 9 项：标记解析、相关性过滤、查了并学会后不再搜、她不查就不搜、没相关结果不编也不记、人名 / 号码 / 联网开关 / 学梗开关、读页、和爹私聊、搜索出错不影响回复。
  - 新增 `tests/test_social_eval.py` 4 项：场景文件格式和对照、离线跑通真实桥接并出报告、核对规则、搜索验收脚本的流程。
  - `tests/test_human_outsiders.py` 的使唤测试改成"她决定查才搜"；周更测试的搜索次数按"大众 + 一个小圈子"改成 2。
  - 容器全量 638 项，除基线环境错误（15 个，以及需要真 Qt 的社区页测试）外通过；全量跑时拦截外网，0 次尝试。
- 没验证：
  - **真实搜索和读页**：`tools/verify_web_search.py` 没有运行（需要真实密钥）；
  - **真实模型的语境评测**：`tools/social_eval.py` 只用假模型跑过流程；她实际分不分得清反讽和调侃、会不会查得太勤，都没有数据；
  - Windows 离线检查；
  - 查梗多出来的一次生成在真实 QQ 上的等待时间。

### T21 预置词库 + T18–T21 测试手册（负责人 2026-11-10 16:18）

- 预置 288 组、336 个常用梗（抽象 112、游戏 71、二次元 67、B 站 53、通用缩写 33），首次启动装成通用义，来源"预置"，可信度 0.55。
  按版本只补新增的词；用户删掉的不再装回；已有通用义的不覆盖。
- 后台推断不再把已有通用义的词降成普通词。
- 新增测试手册 `docs/testing/T18-T22-test-manual.md`：准备、预置词库、少用标点、外人使唤、当场查梗、攻击意图、回归、评测脚本、要发回的结果。
- 验证：
  - 新增 `tests/test_seed_lexicon.py` 9 项：数据格式和四个圈子的数量、不收日常词、只装一次、不覆盖已有通用义、删掉的不装回、本群用法优先、联网学到新义时预置义留作历史、后台推断不降级、聊天里第一天就带进提示且最多 6 条、普通聊天不加。
  - 容器全量 647 项，除基线环境错误（15 个，以及需要真 Qt 的社区页测试）外通过；全量跑时拦截外网，0 次尝试。
- 没验证：
  - Windows 离线检查和真机测试（按手册由负责人进行）；
  - 预置含义的准确性没有逐条核对，靠她用的时候和联网学习纠正；
  - 336 个词在长聊天里匹配的耗时（每条消息扫一遍词表，按现在的写法是 O(词数)，预计在毫秒级）。

### T22 按真机聊天记录修：说到做到、好感分档、像真人、不用逗号、词库第 2 版（负责人 2026-11-10 18:44）

依据：负责人在 `fe226c5` 上的真机测试，NapCat 日志 16:38–17:24。

- **说了不理就真的不理**（`bridge/stance.py`）：她说了"不奉陪 / 懒得理 / 不回了"之后 10 分钟内，这个人的消息只记录、不回、不调模型，一天内再犯翻倍；同一句话两分钟内发第三遍直接不理 5 分钟；对方服软提前结束。爹不受影响。
- **好感分三档**：热络不限条数；一般最多 3 条；冷淡（好感 < 25 或今天冲她来 3 次）只发 1 条，3 次以上一半已读不回，5 次以上直接不理 15 分钟。"人物关系"页显示"现在的态度"，可手动解除不理。
- **像真人**：外人提示改成"绝大多数时候一小句"，不评论梗这个词本身、不分析对方；去掉发出去的 `#数字` 编号；只回这次说话的人。
- **不用逗号**：和外人聊逗号、句号、分号处一律另起一条或短句直接连写。
- **预置词库第 2 版**：+167 个（米哈游相关、二游通用、抽象 / 吵架用语），共 503 个；短字母词按整词匹配。
- 验证：
  - 新增 `tests/test_stance.py` 11 项，场景取自真机记录：说完"不奉陪了"后三条都不回且不调模型、没 @ 也不接、别人照常、到时间恢复、服软提前结束、刷 fw 第三遍起不理、冷淡只发一句 / 不熟最多三条 / 熟人不限、冲她来 3 次后已读不回、5 次后不理、爹不受限、`勾#11` 不外泄而 `C#12` 不误删。
  - `tests/test_human_outsiders.py` 标点测试改成"不留逗号"；`tests/test_seed_lexicon.py` 加第 2 版覆盖、从第 1 版升级只补新词、短字母词整词匹配，共 12 项。
  - 容器全量 661 项，除基线环境错误（15 个，以及需要真 Qt 的社区页测试）外通过；全量跑时拦截外网，0 次尝试。
- 没验证：
  - 真实模型下回复是不是真的变短、不再评论梗，需要真机再测；
  - Windows 离线检查；
  - "人物关系"页的新行和按钮没有在真 Qt 下看过（容器里没有 Qt）。

### T22 补充：外人自称她爹要生气、群聊上下文带上每个人和她的关系（负责人 2026-11-10 19:06）

- 外人说"我是你爹 / 叫爸爸 / 怎么和你爹说话"：当场记"越界"事件（好感、信任都降，这一轮就带着气），提示她绝不认、绝不叫；外人规则加"爹只用来叫你爹"。爹本人不受影响。
- 群里每次回复带"最近在这儿说话的人，和你的关系"（最多 5 人：热络 / 不熟 / 没好感、今天冲她来几次、是否在生气、是否说过不理），并提示按"谁说的、关系如何"读上下文。
- 确认负责人说的两句（"我这儿不奉陪了""不回了你自己玩吧"）都在"说到做到"的识别范围内。
- 验证：`tests/test_stance.py` 新增 5 项（认出自称她爹的说法、外人说了之后好感信任都降且带着气、爹本人说不算、群里四个人各自的关系都写进提示且不带号码、说过不理的人有标注、只有一个人时不加）；容器全量 666 项除基线环境错误外通过，拦截外网 0 次。
- 没验证：真实模型下她是否真的生气、是否按关系读上下文；Windows 离线检查。

### 2026-11-10 T20：撤回外部外观，原鲸娘自绘补帧与逐帧预览（Codex，草稿待审）

- 用户要求回退 #22 并由 Codex 基于原形象绘制流畅动画；#22 已在未合入时关闭，主项目继续原鲸娘。新分支 task/t20-original-whale-animation 从 e709ace 独立实现，没有外部 Aboutme / dsh-pet / Coopanion 图片或音轨。
- 内置 image_gen 基于原 idle / talking / thinking 各画 6 张关键帧，共 18 张；原始输出、完整提示词、归一化图和锚点记录保存于 art/deepseek-whale/animation-keys-v1。离线脚底对齐与双向光流补间形成 24 fps 循环，其余沿用原画小幅运动，共 13 动作 / 960 帧，约 28.3 MiB 图集；不声称每帧均为新画或全套大幅肢体动画已完成。
- 自有 Qt 图集视图：后台解码、单图集缓存、保留原 384 分辨率、初始朝向、动态透明命中、隐藏释放、回收及切回原外观。角色卡只新增动画路径，原图与其他字段保持；未访问真实用户库、修改语音或 Agent。编译只用已有 Pillow / NumPy，锁定离线版本，不新增运行依赖。
- 动画专项 16 项通过；MCP 单独 12 项与动画组合 28 项通过；三次完整 tools/run_checks.bat 均执行，最后 675 项中 667 通过 / 7 跳过 / 1 错误，62.410 秒。未通过项为既有 test_mcp 鉴权测试的本地 WinError 10053，原因未定位，不能记为全量通过。
- 桌宠 Smoke 三次通过，最终 renderer=AnimationAvatar / passed=true / failed_checks=[] / remaining_workers=0；包括透明、后台解码、帧推进、拖拽 / 下落、八姿态、外观往返及聊天链路。最终待机性能仅统计预览进程树：稳定 15 秒后采样 10 秒，工作集 260.6 MiB / 私有 172.5 MiB，单核 CPU 3.3%、20 核整机 0.16%。
- 未完成：用户审图 / 真机观感、另一方代码审查、全量 MCP 错误处理 / 明确延后、长时与高 DPI / 多屏及切换峰值。当前为草稿 PR 候选，非阶段验收或可合并。详情及本机证据见 docs/tasks/T20-original-whale-animation.md。

### 2026-11-10 T20 追加：动作切换过渡（Codex，PR #25）

- 用户要求切换时增加过渡。新增站→工作、站→抱鲸犯困的 8 张中间原画，内置 image_gen 输出、完整提示词与归一化记录保存在 art/deepseek-whale/transition-keys-v1。
- 编译 22 段 / 200 帧一次性过渡，单段约 0.33～0.58 秒，端点无损匹配目标循环；缺少直连时最多两段衔接。大姿态采用头部 / 脚底对齐的单原画逐格变形，避免双图混合的双脸；来源与 SHA256 在 TRANSITIONS.json。
- 播放器新增有限预载、末帧等待、当前画面打断续接、拖拽优先、显示帧透明命中及点播时长修正，修复淡入使身体透明度下降。未改主窗口、Agent、语音或真实用户数据。
- 验证：本轮新增 13 项、累计 29 项动画回归；最终完整 tools/run_checks.bat 退出 0，688 项中 681 通过 / 7 跳过，55.194 秒。Smoke passed=true / failed_checks=[] / remaining_workers=0，真实完成 idle→working→thinking→idle；首轮新增场景的旧挥手干扰已隔离并保留失败证据。之前 MCP 间歇错误本轮未复现，未修改鉴权代码。
- 真实临时预览连续完成 8 个目标和 8 段过渡，42 ms 间隔、包含截图开销的主进程工作集采样峰值 307.3 MiB；录屏 output/transitions/live-transitions.gif。新增资源 17.37 MiB，未新增运行依赖。
- 未验收：用户对切换自然度和原形象一致性的真机确认、另一方代码审查、长时 / 多屏 / 高 DPI / 低性能机器。PR 保持草稿，不能标记阶段完成或可合并。

## 2026-11-10：T20 六组性格动作、低步幅与连续混合（Codex，PR #25 草稿）

### 本轮最终结果（Codex，2026-11-10）

- 新增六组性格 / 日常动作，每组六张，共 36 张：叉腰得意、抱臂气鼓鼓、害羞揪袖、惊讶后仰、捧奶茶偷闲、伸懒腰。得意 / 害羞 / 惊讶分别映射独立动作；新增气鼓鼓、喝奶茶、伸懒腰词汇，右键可点播；轻戳使用新气鼓鼓。角色卡除 renderer.vocab 外逐字段与上一提交一致，persona / voice 未变。
- 低步幅正面 / 背身各六张，共 12 张，保留鲸娘特征。撤下的高抬腿草稿移到 output/personality-first-check/high-step-art，不进入 PR。运行步态上身用自绘图小幅补间；裙摆下提取两条腿做离线小步分层运动，横向最大 10 px、抬升最大 5 px，近直膝。统一双脚基线；不是 24 张手绘或模型训练，也不是运行时新增骨骼引擎。
- 另外给取奶茶、伸懒腰、转身各新画四张中间姿势，共 12 张，柔和小段光流接续。加上前两轮素材，当前运行主循环用 66 张新绘关键帧、专用过渡用 20 张，共 86 张（不计撤下草稿）。
- 当前 20 动作 / 1368 循环帧，36 段一次性过渡 / 372 帧；实际 WebP 图集共 72.05 MiB。三个新中间姿态的往返各 18 帧 / 24 fps（0.75 秒），其他片段保持 8 或 14 帧；端点无损像素 / 来源哈希验证通过。
- 按 Godot Sync / Carry 和 Spine 混合思路，给相容站姿声明混合组，直接实时混合、接续归一化相位，旧图集在 0.22 秒淡出内继续播放，结束 / 隐藏即释放。被打断时使用当前显示图，拖拽仍优先。大姿势使用新画片段，其他轮廓补间柔和续接，不再在中间时刻硬换一张线稿。技术来源见 [动画参考](T20-animation-references.md)，没有复制外部代码 / 人物 / 音轨。
- desktop_pet.py 从仅 x 方向改成受当前屏幕边界约束的二维路线，浮点累计位置保持斜向总速度一致。起停采用加速度 / 制动，基础速度 42 px/s 随缩放变化；移动距离驱动脚步，加载 / 转身 / 表演等待时不滑走。向上用背身，其余使用正面及镜像，两套透视支持八种路线，并非八套独立透视画。右键“走几步”可点八方向和停止，闲时散步也有小范围上下 / 斜向变化；静音 / 专注仍禁止主动散步，聊天 / 拖拽 / 下落 / 隐藏会打断。
- 最终 tools/run_checks.bat 退出 0：706 项，699 通过 / 7 跳过，56.602 秒。Smoke passed=true / errors=[] / failed_checks=[] / remaining_workers=0；六组性格动作按对应目标显示，三段实际行走覆盖斜上 / 竖直 / 水平及正反步态，全部到达。原站坐、聊天、拖拽、透明和外观往返检查仍通过；同组思考改为直接混合，Smoke 已按该行为检查实际目标。
- 真实播放器另外录制六组动作和五条路线，每条步态均实际推进 24 个相位，全部到达 / 无错误。84 ms 截图间隔、含 PNG 录制开销的主进程工作集采样峰值 328.8 MiB、最低 188.1 MiB；不含启动器 / 控制台，不作为长期 / 全机器峰值。录屏与报告：output/personality-animation/live-walking.gif、live-walk-report.json。
- 第一轮 702 项检查 / Smoke 与原迈步录屏已通过，但用户随后要求低抬脚与更流畅切换，因此仅保留在 output/personality-first-check 作为历史，最终证据以上述新录屏和完整检查为准。普通光流大幅迈步曾有重影，改为清晰单原画 / 低幅腿层后未沿用该草稿。
- 素材与完整提示词：art/deepseek-whale/personality-keys-v1/prompts.json；locomotion-keys-v2/prompts.json；transition-keys-v2/prompts.json。来源 / 参数 / 哈希在 SOURCE.json 与 TRANSITIONS.json；离线重建用 tools/build_original_animation.py（串联新动作与过渡），未增加运行依赖。新离线脚本 / 桌面行走 / 播放器 / 动画解析器 / 测试 / Smoke 为本轮代码范围，不改 Agent、main_window.py、存储结构或现用语音。
- 待验收：用户对低步幅、脚步与路线配合、相位混合、大姿势衔接的真机观感；另一方在最新提交审查；长期、多屏、高 DPI、低性能机器。艺术验收不能被单元测试替代，PR #25 继续草稿，不标记可合并。

## 2026-11-10 · T20 唯一参考十动作与旧版回退（Codex，草稿待验收）

- 只以用户本轮上传的 user-identity-reference.png 为本批角色造型来源，使用内置 image_gen 绘制左右行走与八个互动：元气挥手、屈膝礼、歪头观察、捂脸偷看、三次小跳、自抱晃动、鼓脸跺脚、捂嘴偷笑。每组六张关键姿态，另绘统一站姿；左行走单独细化三分之四朝向。首稿保留审计，不参与运行。完整提示词与输入关系在 art/deepseek-whale/reference-motion-v1/prompts.json。
- 本批每项 384×384、12 FPS、120 帧、精确10秒，共1200帧；另有120帧标准站姿。正常站姿头顶约18%、脚底约85%、人物高度约67%；屈膝 / 跳跃保持同一人物尺度而允许姿态高度变化。全部帧实际最小边距54px（14.1%），超过10%要求；所有八个互动首末共用同一标准站姿，行走首末同一相位。
- 不是1200张手绘或视频模型输出。小段光流与单原画采样补间不交叉叠出第二张脸 / 第二双手；八个步态周期中只在两周期使用闭眼关键姿态；三次小跳在2～6秒中完成，分段接续连续。WebP无损编码使用exact=True，已对编码后RGBA首尾比较；来源 / 图集SHA和构图记录在新包SOURCE.json。
- 新运行包 animation-reference-v1 的11个图集共68.44MiB。原 animation-original 的20动作图集保留不覆盖，通过replacementPack在表达层合成，默认共30动作、保留22段旧大姿势过渡。其他旧动作解码时等比构图适配到新版站姿，不改变角色身份或记忆。
- 已按用户选择接入：点击 / 摸头、挥手 / 害羞 / 生气、鞠躬 / 歪头 / 摇摆 / 小跳及新增词汇映射对应动作。右键动画菜单优先列十项新动作，去除兼容别名重复。右键“动画版本 → 旧版动画（回退）”可切换；版本偏好按角色保存，实际来回切换通过。
- 新模式使用独立左向 / 右向原画，原地动画不把窗口位移烘进素材；桌面走动由实际距离推进，八周期换算不再受帧数 / fps改变影响，路线以半个步态周期规划停步。新模式不再整图镜像待机、衣服或鲸鱼图案；转向 / 重设路线保留当前显示画面，加载完成后才继续移动。本批按最新规格提供左右行走；旧二维走动留在回退模式。先前未提交的二维基准图实验已归档 output/walking-fix/superseded，不进入新素材包。
- 最终 tools/run_checks.bat 退出0：716项，709通过 / 7跳过，60.459秒。Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0；十个新动作逐一实际加载，全部120帧 / 12FPS；左右实走均到达；新→旧→新回退与旧版站坐过渡仍通过，原聊天、拖拽、透明、八姿态和外观切回检查保留。设置和测试均为临时库，未读取.env或真实用户库。
- 最终实际播放器录屏：90帧、84ms截图间隔，左右各120px均到达、各实际推进60个图集帧；using_reference=true、无错误。含PNG录制开销的主进程工作集采样峰值371.7MiB、最低207.3MiB，不含启动器 / 控制台，也不作为长期峰值或350MB目标验收。文件：output/reference-motion-v1/runtime-walking.gif、live-walk-report.json。
- 交付：output/reference-motion-v1/all-actions.gif、walking-pair.gif、各动作GIF / 透明动态WebP、frames/<动作>/000..119.png，共1200张PNG；DeepSeek-10-actions-12fps.zip为161.1MiB。完整离线重建 tools/build_original_animation.py 已串联本批构建；不增加依赖，不改Agent、main_window.py、数据库结构或现用声音。角色卡逐字段核对，voice / persona与上一提交一致，仅renderer.vocab扩展。
- 待验收：用户对脸型 / 发型 / 服装细微一致性、肢体遮挡与插值自然程度的审图；这仍是有限关键姿态补间，不能由帧数 / 边距测试证明完整物理惯性。另一方最新提交代码审查，以及长期、多屏、高DPI、低性能机器和不含录制开销的内存验证未完成。PR #25继续草稿，不标记可合并。

## 2026-11-10 · T20-动画同步最新基线与关闭保护（Codex，草稿）

已rebase到collaboration-baseline 5286e1d，联合保留双方任务 / 实施记录；社交T20与动画T20编号重叠，动画清单使用T20-动画，分支和文件名沿用旧名。没有覆盖已合入社交 / 词库代码。

最终tools/run_checks.bat退出0：803项，796通过 / 7跳过，103.802秒；Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0，十动作 / 左右走动 / 新旧回退均通过。新增关闭保护：最后保存一次位置，关闭后的移动 / 缩放与晚到回调不再写设置；首次同步日志中的两次临时库OperationalError在最终完整检查中未再出现，原日志保留审计。

新基线录屏91帧，左右各120px到达、各推进60个图集帧，using_reference=true；短时主进程工作集峰值348.3MiB、最低204.4MiB（含PNG录制，不含启动器 / 控制台），不作为长期内存目标验收。最新报告与GIF仍在output/reference-motion-v1；同步前报告 / 录屏另存pre-baseline前缀。本批图集与动作逻辑不变；用户审图、另一方审查、长期 / 多屏 / DPI和低性能机器仍未验收。

## 整帧行走替换与验证（Codex，2026-11-10）

- 按用户最新要求撤下拆手脚 / IK 实验，改用完整人物原画帧。左右各一张3列6行清稿，使用其中17张独立完整姿势，按17×7+1组成120帧；末帧直接复用首帧。内置image_gen生成，原始用户图为唯一身份参考；全周期清稿统一脸型、服装与配饰，过程输入和完整提示词在art/deepseek-whale/walk-cels-v1。不是120张不同新画，不是视频生成模型输出。
- 只整帧裁格、共同等比缩放、水平居中及接地线对齐，不切开手脚、不扭曲关节、不用光流生成行走中间帧。固定图纸含少量跨格留白，裁格外扩后只保留本格完整人物连通主体，避免切掉鞋尖或带入邻格碎片。运行两套仍384×384、10秒、12FPS、120帧，首尾编码后RGBA完全一致，左右最小安全边距58px。
- 参考aboutme-pets的完整画面图集及逐动作JSON字段组织方式，新增walking_left.json / walking_right.json；项目仍沿用自己的播放器和聚合清单。导出链tools/build_walk_cels.py → build_reference_motion.py → build_original_animation.py已接通。运行每帧与17张整帧PNG逐像素对应，并记录序列和哈希。
- 新模式桌面步速按图集距离/时长计算，当前300px显示宽度为28px/s；保持距离驱动、独立左/右方向及原有版本回退。八个互动WebP逐字节与2fe42f0一致，旧版资源保留。新包11个WebP共67.17MiB。分层实验与重复同侧迈步的草稿回收到output，不纳入PR。
- 最终tools/run_checks.bat退出0：807项，800通过 / 7跳过，114.938秒。Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0；十动作加载、左右到达、新→旧→新回退、原聊天/拖拽/透明等检查均通过。新增整帧图集像素/比例/来源检查和图集周期匹配步速回归。测试与实测使用临时库和Mock，未读取.env或真实用户库。
- 实际播放器录屏120帧、84ms截图间隔；左右各120px均到达，各推进52个图集索引，using_reference=true、无错误。含PNG录制开销的主进程工作集短时采样峰值395.4MiB、最低192.0MiB；不含启动器/控制台，不作为长期峰值或350MB目标通过。记录output/reference-motion-v1/runtime-walking.gif、live-walk-report.json。
- 交付预览walking-pair.gif、可暂停/逐帧拖动的独立walking-preview.html、all-actions.gif、1200张PNG和DeepSeek-10-actions-12fps.zip（250.03MiB），均在output/reference-motion-v1。按帧播放与几何检查不能证明动画已自然；面部视角/服装细节一致性、两腿遮挡和脚底接触仍待用户审图。另一方最新提交审查、长期内存、多屏、高DPI与低性能机器未验证；PR #25保持草稿，不标记可合并。

- 重建验证边界：本轮实际执行了左右整帧导出，并单独验证compile_action入口返回120帧与一致端点；未再次重导旧版全部原画/过渡素材。上面的“导出链接通”指代码已接入，非本轮全量重导出记录。

## 整帧补画与24FPS播放（Codex，本轮结果）

- 用户要求“多画几帧让动作更加流畅”。左右各新增17张完整人物过渡原画，和原17张交替组成34张/周期；旧17张PNG保持像素一致。新增完整帧由内置image_gen绘制，不拆手脚、不用光流造帧；相邻A/B图、统一画风输入、最终清稿和完整提示词存于art/deepseek-whale/walk-cels-v2。初稿背景残留和新旧画风差异经清理后再导出，未采用稿放output回收区。
- 行走升级为24FPS、10秒、240帧（34×7+2个闭环帧），首末RGBA一致，左右最小边距58px；八个互动仍12FPS/120帧，WebP与a39378d逐字节一致。距离参数280和名义步速28px/s不变，保留旧版回退。不是240张独立新画。
- 240格384px全图会超过既有32M像素限制，因此新增可选frameMap时序引用，物理图集仅存34张完整原画（8列5行，5,898,240像素）。索引长度、整数类型、范围及原图集像素上限严格校验；当前帧、过渡中旧帧及构图处理使用实际格索引。默认无frameMap的旧资源兼容。每个运行帧都逐像素对应完整原画，未通过运行时扭曲来补画。
- 两张行走WebP分别1,838,762与1,950,256字节，新包全部11张WebP共58.18MiB。离线编译和总预览支持混合FPS，逐动作JSON带FrameMap，项目独立逐帧网页已适配。实际运行了tools/build_walk_cels.py导出；旧版所有原画/过渡的全量重导出本轮未再次执行。
- tools/run_checks.bat退出0：809项，802通过 / 7跳过，130.039秒。Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0；十动作加载、左右到达、新旧回退与原交互通过。新增真实Qt红蓝格回归覆盖帧映射和淡出旧帧；旧原画保留、新增原画来源、等比对齐与帧像素匹配检查均通过。临时库/Mock，不读取.env或真实用户库。
- 实际播放器42ms间隔录制237张，左右各120px均到达，各实际推进103个逻辑帧索引，using_reference=true、无错误。主进程工作集短时采样峰值345.1MiB、最低206.5MiB，含PNG录制开销，不含启动器/控制台，不代表长期内存或350MB目标验收。报告live-walk-dense-report.json，录屏runtime-walking-24fps.gif。
- 新交付在output/reference-motion-v1：walking-pair-24fps.gif、walking-before-after.gif（同速12/24FPS对照）、walking-preview-24fps.html、all-actions-dense.gif、1440张PNG和DeepSeek-10-actions-walk24fps.zip（276.51MiB）。原12FPS预览和包保留。全套自动检查不能证明自然度与角色细节完全一致，仍待用户对补帧连贯性/遮挡的真机审图、另一方最新提交审查，以及长期/多屏/DPI验证。PR #25继续草稿，不标记可合并。

## 2026-11-11 · T20-动画改为Aboutme画面（Codex，待人工验收/合并）

用户否定此前自绘并要求直接采用Aboutme；上面各阶段自绘记录仅为历史，不再是本分支当前交付。### 当前范围

用户明确要求直接切换 hunmer/aboutme-pets，并授权清理先前不合格自绘。
停止自绘迭代，固定上游提交 1221697ec20c2057829f9d7fcbd316fee8b3df65，
选择 dsh/dsh1 共21个源动作。本机打包成22个逻辑动作（左/右行走共享源图）。
上游完整帧、原12FPS、统一512×288，行走采用 moving 标注15～94帧；
右行走镜像，角色不重画、不拆肢体、不额外插帧。仅支持左右路线。
人格、关系、音色、聊天渠道与Agent不在本轮变更范围。

### 代码与安装

- 新增 tools/install_aboutme_pet.py、tools/aboutme-pet.lock.json 与 docs/aboutme-pet.md，
  固定路径/大小/SHA256；上游贴图及转换包放 Git 忽略的本机目录，不提交公开PR。
- 安装沿用已有 Pillow 12.3.0，锁定 tools/aboutme-requirements.txt，不增加运行依赖。
  先联网下载；--offline 可从校验缓存重装。运行时不联网、不自动下载。
- 角色默认指向 animation-aboutme；缺少本机包时退回基线立绘。
  沿用后台Qt解码、有限预载、可打断淡入、透明穿透、聊天和拖拽。
- 移动距离驱动帧相位，按原片段时长40画布px/s；不猜测半步相位去缩放步态。
  点击别名解析到实际源动作，菜单保留左右入口，右向明确标记镜像。
- 保留通用解析、frameMap、过渡和移动回归，退休只针对失败素材/生成工具的测试。
  新增合成图集完整格顺序/透明、缓存拒绝、离线回退、方向和固定步速测试。
- 不从本机 Coopanion 读取或导出。归属与限制见上游 README/NOTICE 和本接入说明。

### 回收

426个本任务新增的旧图纸/运行包/专用工具与测试，共227,012,097字节；
另回收14个旧预览目录中2,604个文件，共1,087,123,984字节。
均移动到 output/recycle/aboutme-migration-2026-11-11，可恢复，未永久删除。
receipt.json 与 old-previews/receipt.json 分别列出原路径/数量。
用户原始参考图 user-identity-reference.png、T5/T6基线立绘/骨骼及协作者
未跟踪 locomotion-locked-v1 未动；其他任务目录未清理。

### 验证（Windows）

- tools/run_checks.bat 退出0：792项，785通过/7跳过，107.018秒；
  Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0。
  实际22动作加载、待机→忙碌→思考→待机淡入、左右60px到达、
  切基线立绘再切回Aboutme及原聊天/拖拽/缩放/透明穿透等检查通过。
- 点击/菜单小修正后针对性72项退出0，2.689秒；Pillow可选测试守卫随后仅改导入边界。
- 临时库+Mock实际窗口录制93帧，84ms间隔：左右各120px到达，
  各推进36个帧索引，using_aboutme=true、errors=[]。
  主进程工作集短时峰318.1MiB/最低235.5MiB，含PNG录制开销；
  不包含启动器，不代表长期内存目标验收。output/aboutme/live-report.json、
  runtime-walking.gif。本机21张WebP共121,907,918字节。
- 未读取.env、真实用户库或私有素材；未联网调用模型、语音或麦克风。
- 未验证：源动画的自然度/循环接缝人工验收、长期内存、多屏/高DPI/低性能设备、
  另一方最新提交代码审查。未合并到D盘基线，不标记“可合并”。
## 2026-11-10：项目计划书 v4（纯文档，Claude）

- 新增 `docs/project_plan_v4.md`，按负责人要求作为下一阶段的开发依据：七项功能（模型自由切换、可扩展人格包、持续情绪、自己的生活、真正退出、记忆召回与上下文预算、圈子与跨天办事）、现状基线（`5286e1d`）、四条硬底线、开发参考的成熟方案及许可边界、四个阶段的开发顺序、验收方式与待实测事项。
- 原则为“先做起来，再按实际效果调整”：指标是初始目标，不作准入门槛；只有四条硬底线必须满足。
- `AGENTS.md` 的计划书指向改为 v4，v3 保留为历史；`docs/tasks.md` 登记 PLAN-V4。
- 没验证：本轮只写文档，未改运行代码，未运行离线检查；计划中的所有功能都待实现，待实测事项均未执行。

## 2026-11-10：V4-1a 模型配置与按能力解析（Claude）

- 新增 `src/aicompanion/providers/`：`models.py`（账户 / 模型 / 绑定 / 快照与 DeepSeek、MiniMax 预设）、`credentials.py`（`env:` 与 DPAPI 加密的 `stored:` 凭据）、`store.py`（`app_settings.model_config` 一条带版本的 JSON）、`router.py`（四层查找、按用途的思考参数、客户端缓存、搜索与 Agent 目标）。
- 新增 `src/aicompanion/net.py`，合并原来三份 `NoRedirect`；`OpenAICompatibleClient` 与智谱语音客户端改用它，鉴权头不再跟随跳转。客户端新增 `extra`、`max_tokens` 参数，不传时请求体与旧版完全相同。
- `CharacterRegistry.client_for` 改由解析器实现，接口不变。
- 验证：容器内 Python 3.13 + PySide6 6.12 离线单测 `python -m unittest discover -s tests`：771 项通过、38 项跳过（新增 `tests/test_model_routing.py` 26 项，含本机端口模拟 301/302/303/307/308 跳转，确认目标端收不到 Authorization；换回 urllib 默认打开方式时该测试失败，说明旧代码确有泄露）。
- 没验证：Windows 上 DPAPI 加解密（容器不是 Windows，只用假加解密器测试）；`tools\smoke_pet.py`（容器缺音频设备，基线代码同样崩溃，需在 Windows 跑 `tools\run_checks.bat`）；真实 DeepSeek 接口对 `thinking: disabled` 的响应（升级后旧配置走 DeepSeek 官方时会默认关闭思考）。

## 2026-11-11：V4-1b 去掉旧模型配置、调用点改用快照、Breeze Blue 语音合成（Claude）

- 负责人决定开发阶段不兼容旧配置：删除 `.env` 的 `LLM_*`（Settings 新增 `offline`，`AICOMPANION_OFFLINE=1` 或发布自检时启用）、`llm/factory.py`、角色包里的模型推荐和 `CharacterRegistry.client_for`。模型只来自设置里的模型配置（角色单独指定 → 全局）；聊天没配置时以离线模拟运行并在标签上提示，识图没配置不看图，搜索没配置用维基百科，办事没配置不启用。
- 调用点：ChatController 每轮按角色与用途取客户端（`client()`），去掉 `llm_client`；QQ 识图、查梗二次生成、记忆 / 关系 / 社区分析、目的提炼、日语翻译都在启动后台线程前取好快照；识图“不支持”标记按配置修订区分，推理耗尽额度不再误记为不支持看图；社区学梗按角色的搜索配置建搜索服务。
- 办事：`AgentTarget` 驱动 DSH，子进程只带白名单系统变量和选定 Key，换 Key 或路由时重启运行时；Harness 不认识路由或模型时任务失败，不退回 DSH 默认模型。
- 语音 Key 不再按主机名借用聊天 Key。
- 新增 Breeze Blue 语音合成：模型配置新增 `tts` 能力（音色 ID、语言、语速、音量、固定语气说明，可按角色设置）；`speech/breeze_client.py` 按官方接口 `POST /v1/text-to-speech/{voice}?output_format=wav_24000`（`xi-api-key`）合成；朗读方式下拉新增 Breeze Blue。语音缓存键包含音色、模型、参数和 Key 修订。
- 复用：`net.fetch` 抽出可取消、限大小的下载循环，千问和 Breeze 共用；`validate_wav` 可指明来源。
- 验证：容器离线单测 784 项通过、38 项跳过（新增 `tests/test_breeze_tts.py` 12 项，含本机端口真实往返和跳转不跟随；`test_dsh` 新增子进程只带选定 Key、换 Key 重启运行时、未知路由失败三项）。
- 没验证：Windows DPAPI；`tools\smoke_pet.py`（需 Windows）；真实 Breeze Blue / DeepSeek / MiniMax 接口与 Harness 的 MiniMax 路由；模型设置页尚未完成（V4-1c），在此之前只能通过代码或测试写入配置。**升级后旧 `.env` 的模型配置不再生效**，需在 V4-1c 的设置页重新配置。

## 2026-11-11：删除爱蜜莉雅角色（Claude）

- 负责人决定删除爱蜜莉雅。删除角色包 `characters/emilia`、同人立绘与网格动画资源（`assets/live2d/Emilia`、`emilia_rig.js`）、人设预设 `domain/personas.py`、专用自检 `ui/emilia_smoke_check.py` 与 `tools/verify_persona.py`；去掉 `--emilia-smoke-test`、`tools/smoke_pet.py --emilia`、`AVATAR_PREVIEW` / `CHARACTER_PRESET` 配置，以及设置页的两种艾米莉娅形象模式。网页渲染只保留用户导入的 Cubism 模型。
- 默认角色改为鲸娘：`CharacterProfile` 默认 ID 和表结构的 `DEFAULT` 改为 `deepseek-whale`；数据库初始化写入同 ID 的占位角色，角色包同步时换成包里的名字与人设（用户改过的不动）。
- **需要删除旧数据库重新生成**（开发测试阶段，负责人同意直接删库）：旧库里爱蜜莉雅的角色、聊天和记忆不再有入口。
- 自检：桌宠自检的“切换角色”改为切到一个临时自建角色再切回；语音 / 通话自检不再等艾米莉娅形象加载。README、`.env.example` 同步（也去掉了 `LLM_*` 说明）。
- 验证：容器离线单测 779 项通过、38 项跳过（删去爱蜜莉雅专属测试，新增占位角色被角色包人设替换且不覆盖用户编辑的测试；v5 旧库升级夹具保持原样）。
- 没验证：Windows 上 `tools\run_checks.bat` 的桌宠自检、语音与通话自检；打包后的 `--pet-smoke-test`。

## 2026-11-11：V4-1c 模型设置页与阶段一实机测试手册（Claude）

- 新增 `providers/admin.py`（ModelAdmin）：添加预设账户（DeepSeek、MiniMax 国际站 / 国内站、Breeze Blue，自动带上同一家的预设模型）或自定义兼容服务；Key 加密保存或指向环境变量名；编辑时 Key 留空不修改；地址必须是 https（本机调试允许 127.0.0.1），拒绝带用户名密码的地址；删除账户时作废保存的 Key，仍在使用时拒绝删除；自定义模型由用户声明能力；按能力选择时检查模型是否声明了该能力，办事自动对应 Harness 内置路由（DeepSeek → deepseek-official，MiniMax 国内站 → minimax-cn，国际站 → minimax），未验证的服务不能用于办事；测试聊天、试听语音。
- 新增 `ui/model_page.py`（设置 → 模型）：账户表（Key 一栏只显示来源与是否可用）、添加 / 修改 / 删除账户、添加模型；按“全局默认 / 某个角色”设置聊天、识图、搜索、办事、语音合成（含 Breeze 音色、语言、语速、音量、语气）；显示当前生效的模型和音色；测试与试听在后台线程执行。设置窗口保存时先保存模型页，失败则提示且不关闭。
- 新增 `docs/tasks/V4-1-test-manual.md`：阶段一实机测试手册。
- 验证：容器离线单测 789 项通过、38 项跳过（新增 `tests/test_model_page.py` 10 项，其中 3 项离屏驱动真实页面）；离屏截图检查页面布局。
- 没验证：Windows 实机（DPAPI、真实服务、桌宠自检）——按测试手册由负责人执行。


## 2026-11-11：V4-1c 实机反馈修复（Claude）

负责人第一次实机试用后反馈的问题：

- **模型页选择退出后丢失**：选择改为“选了就保存”（下拉框、音色 / 语气输入完、语言 / 语速 / 音量变化时），去掉“应用”按钮；载入范围时不触发保存。选了语音合成但还没填音色时只在状态栏提示，不弹窗，填好后自动补存。
- **同名账户在下拉里重复**：新增或改名时名字重复自动加编号（DeepSeek → DeepSeek 2）；下拉显示名在仍然重名时带上账户编号。
- **办事报“Harness 不支持 minimax-cn / MiniMax-M3.1-Flash”**：办事下拉只列出锁定版 Harness 内置目录里的模型，选择时也校验；旧配置里留下的不支持模型不启用，提示说清是“全局默认”还是“这个角色的单独设置”、该去哪里改、可选哪些模型。办事项旁边加说明：全局默认会用于所有没有单独设置的角色。
- **完整朗读会念出文件名**：朗读前把代码块读成“一段代码”，链接读成“链接”，路径和文件名读成“那个文件 / 那个文件夹 / 文件”，并去掉 Markdown 符号。
- **打开设置会打断回复**：打开 / 关闭设置不再挂断通话、不再中断回复和办事；通话中改了麦克风、回声后端或收音方式只重启收音。
- **语音默认**（负责人新要求）：收音方式默认改为“按住说话”（默认收音键右 Ctrl），选项顺序调整；配置了千问声线时不再自动打开朗读，朗读和通话都默认关闭。已保存过收音方式的旧库沿用原值（删库后生效）。
- 验证：容器离线单测 794 项通过、38 项跳过（新增重名账户、办事过滤与提示、选择自动保存、默认按住说话、朗读不念路径等测试）。
- 没验证：Windows 实机；“非完整朗读时偶尔回复失败”的原因尚未定位，等负责人补充现象。

## 2026-11-11：处理 Codex 对 #27～#30 的审计（Claude）

按负责人“凭据串用 / 泄漏、数据破坏、未审批副作用优先”的范围，R1、R2、R5 已修；R3 按建议暂禁用并写明；R4、R6、R7 也一并修了。修复提交在各自引入问题的分支上，再依次合并到后续分支（不改写历史）。

- **R1 凭据快照**（#27）：`CredentialStore.get` 可带快照的凭据修订号和目标地址，在同一次读取里核对；保存的 Key 记录所属地址（`save(endpoint=)`，只改地址时 `bind` 改登记并递增修订）。`client_for`、`secret`、搜索 / 语音 / 办事目标都按地址取 Key，对不上就放弃这次请求；QQ 识图在快照后账户被改时不看图。模型页新增和修改账户时登记地址，且先改凭据再存新地址。
- **R2 Harness managed 凭据**（#28）：DSH 默认先读 `$DSH_HOME/.credentials.yaml`，优先级高于环境变量。补丁把 credentials 插件的 path 指向本应用每次启动清空的自有文件；用户原有凭据文件不读、不删；运行时当前目录换成专用空目录，`$DSH_HOME` 或当前目录里有 `.env` 时拒绝启动并提示。用真实 DSH 0.2.1rc2 运行时（Linux 版）+ 本机模拟 Messages 服务 + 假 Key 复现：修复前服务收到 managed 旧 Key，修复后收到选定 Key；该回归已写进 `tests/test_dsh.py`（未安装运行时时跳过）。
- **R3 MiniMax 办事**（#30）：llm-pi-ai 的 MiniMax 路由尚未在补丁里激活，暂不开放：办事下拉只列 DeepSeek，选择或旧配置里的 MiniMax 办事都提示“暂未接通”。登记为已知问题，后续接入并用实际 configOptions 验证。
- **R4 Breeze 排队句子**（#28）：句子入队时取好完整的 `TtsTarget`，合成和缓存键都用它；排队期间改音色或账户只影响新句子。
- **R5 本机地址校验**（#30）：新增 `check_base_url`，新增和修改账户共用：https，或 http 加真正的回环 IP（`ipaddress` 判断）；拒绝带用户名密码、查询参数、片段的地址。
- **R6 试听合成两次**（#30）：新增 `SpeechOutput.play_audio`，试听把刚合成的 WAV 直接交给播放链路，不再合成第二次，也不再改动朗读开关和朗读方式。
- **R7 流式代码块**（#30）：`SentenceBuffer` 跨句记住是否在 ``` 代码块里，代码块整体读成一次“一段代码”，结束后正常朗读；气泡和历史仍是原文。
- 验证：容器离线单测 801 项通过、39 项跳过（真实运行时测试在容器里另外用下载的运行时跑过并通过）。
- 没验证：Windows 上的真实运行时回归（`tests/test_dsh.py` 的 RealRuntimeCredentialTest 会在装了运行时的 Windows 环境里执行）；真实 DeepSeek / Breeze 服务。
## 2026-11-11：表情图库减负（Claude）

负责人实机反馈：图库里的图默认都是“待审核 / 停用”，没有批量操作；很多人不知道许可怎么填、来源在哪，含义也不该让用户手填。

- **自己的图默认可用**：放进图库目录或手动导入的图，没写许可时一律按“自己的图（自用）”登记并启用，作者、来源自动填“我自己 / 本机图库”。素材清单导入的图仍按清单写明的许可，没写的显示“许可待确认”。旧版扫描登记成“待审核、来源不明”的图在下次扫描时按新默认转为可用；用户之前明确停用的保持停用。
- **含义自动识别**：没写含义的图（含清单里没写含义的）排进识图队列，复用 QQ 表情收集的识图分析和每日额度；桌宠打开表情图库页时在后台识别（线程挂在主窗口上，关掉设置也不中断），QQ 服务收到表情时也会顺带识别。识别出来之前先按分类名使用。用户自己放的图被识图判断为“不适合”时只记原因、不自动停用（收集来的图仍会停用）。用户手改过的含义不被覆盖；含义清空保存或点“重新识别含义”即重新排队。
- **界面**：许可改成中文下拉（只能选，不用输入许可名），去掉作者、来源、表达意图、情绪输入框；表格可多选，新增“全选 / 启用选中 / 停用选中 / 重新识别含义 / 设为选中图片的来源”；“导入图片”可多选；状态列显示“可用 / 停用 / 许可待确认 / 识别含义中”。需要署名的许可缺作者时提示改选“自己的图（自用）”。
- 验证：容器离线单测 796 项通过、38 项跳过（新增批量操作与清单许可、自动识别含义两项界面测试；更新了原先断言“用户新增的图待审核”的 5 处测试）；离屏截图检查页面布局。
- 没验证：Windows 实机；真实识图模型给图库图片识别含义的效果和耗时。
- Codex 审查（dd09b30）四项 P2 的修复：
  - R1 旧清单记录：`from_pack` 是本次才写的，旧版 `import_pack` 的记录没有。扫描迁移改为只认“许可未知、原作者和来源都空”的旧记录（旧版扫描登记的样子）；写了原作者或来源的旧记录保持待确认，作者、来源、含义原样保留。含义是否为默认值只看旧记录自己的 `annotation_defaults`（更早的记录：含义为空或等于分类目录名），不看本次扫描补的默认值。
  - R2 金额额度：图库入口在启动和每张图开始前都检查 `Quota.check_silent('qq')`（与 QQ 后台分析一致）；到限停止，队列保留。
  - R3 重复调用：分析层按素材领取（`collect.claim / release`，本进程内图库页各 worker 与 QQ 服务共享），正在识别的图其他入口跳过，不重复调用模型、不重复计数。关掉设置后原 worker 继续跑的行为不变。
  - R4 未审核 / 停用：`pending()` 只取已审核且启用的图；未审核、停用的留在队列里，不调用模型、不扣次数、不算失败，确认来源或重新启用后接着识别（图库页启用 / 设来源后会触发识别）。文件丢失等本地失败只记一次失败、不扣当天额度。
  - 验证：每项新增一条回归测试（在修复前的代码上均失败）；容器离线全量 807 项通过、6 项跳过。
  - 没验证：Windows 实机与 `tools\run_checks.bat`。
- 实机反馈（2026-11-11）：点“重新识别含义”后一直显示“识别含义中”。本机没有识图运行日志，原因看不到：额度（金额 / 每天张数）到限、识图未配好或接口报错时，后台识别都不出声地停下。处理：
  - 图库页显示停下的原因：摘要里“等识别含义 N（…）”和点按钮后的状态栏都会写明是金额额度用完、每天张数到上限、识图没配好、图是停用 / 待确认、收集已关，还是另一处正在识别；当天张数已满时不再开空线程。
  - 识图失败记原因（设置 `sticker_analysis_error`，只记 HTTP 状态码或异常类型、不记错误正文）；一批结束后分开显示“识别成功几张 / 没识别出来几张及原因”，不再把失败也算成“已识别”。后台线程出异常也显示出来。
  - Codex 复审（4df931f）R5：图库页和 QQ 服务每张开始前复核 `vision_ready()`（含“接口拒收图片”标记）；中途关识图或首张被拒收后，本批结束，其余保持待识别、不扣次数、不记失败。
  - 验证：新增 4 条测试（每天上限 / 识图失败的提示、R5 两个场景、失败原因不含错误正文），在 4df931f 上失败、修复后通过；容器离线全量 811 项通过、6 项跳过。
  - 没验证：Windows 实机；真实识图模型的回答格式。
- 实机数据库排查（负责人同意只读查看，2026-11-11）：171 张自有图，1 张识别成功、47 张各失败 2 次、1 张失败 1 次，当天识别计数已到默认上限 100，所以之后一直停在“识别含义中”。95 次失败在用量表里都没有记录，说明请求在拿到正常结果前就失败了；MiniMax 常把限流、余额、审核、参数错误放在 HTTP 200 的 `base_resp` 里返回，客户端会报“响应格式不符合预期”，这与看到的现象一致，但具体错误码当时没有记下。处理：识图失败原因加记平台错误码和中文说明（不记平台返回的正文）；识别失败的图状态显示“含义没识别出来”。

## 2026-11-11：办事接入 MiniMax（Claude）

负责人要求引入 MiniMax 办事（处理审计 R3 登记的已知问题）。

- Harness 的 llm-pi-ai 多供应商路由默认不注册路由。`dsh_backend.runtime_patch(home, target)` 只在本次选的是 MiniMax 时，在补丁里激活那一条路由（`minimax` 或 `minimax-cn`）：`apiKeyEnv` 指向选定 Key 的环境变量（补丁里只有变量名），`baseURL` 用账户地址的主机加 `/anthropic`（Anthropic 兼容接口，Key 只发往账户登记的主机），并列出可用模型。内置目录没有 MiniMax-M3.1-Flash，补丁里按官方文档声明（100 万上下文、支持图片）；不声明思考档位，请求不带 `thinking` 参数（这个模型关不掉思考，发 disabled 会被拒绝），由服务端按默认深度思考。
- 可办事的 MiniMax 模型：MiniMax-M3.1-Flash、MiniMax-M3、MiniMax-M2.7、MiniMax-M2.7-highspeed（后两个需要在模型页自己添加）。路由、地址或 Key 变化时重启运行时。
- R2 的凭据隔离保持不变：managed 凭据文件仍指向本应用的空文件，MiniMax Key 只从选定的环境变量来。
- 验证：容器离线单测通过；用真实 DSH 0.2.1rc2 运行时（Linux 版）+ 本机模拟 Anthropic Messages 服务 + 假 Key 跑了 `minimax` / `minimax-cn` × M3.1-Flash / M3 共 4 组，每组两轮：Harness 接受模型选择，请求发往 `/anthropic/v1/messages`，带的是选定 Key（预置的 managed 旧 Key 没被用），M3.1-Flash 请求里没有 `thinking` 参数，第二轮带着上一轮的思考块正常完成。该回归在 `tests/test_dsh.py`（未安装运行时时跳过）。
- 没验证：真实 MiniMax 服务（需要负责人用自己的 Key 按测试手册第 8 节试）；M3.1-Flash 默认思考深度下的速度和费用；Windows 上的真实运行时回归。

### 2026-11-11 补充：Codex PR #32 审计 P1（跳转带 Key）修复（Claude）

Codex 在 Windows 上用真实 Harness 复现：llm-pi-ai 路由的打包 fetch 默认跟随跳转，账户主机返回跨主机 307 时 `x-api-key` 原样带到新地址；Harness 配置里没有关闭跟随跳转的开关，Python 侧的禁止跳转也管不到子进程。

- 新增 `agent/key_relay.py`：MiniMax 办事时，桌宠进程在 127.0.0.1 随机端口起一个 Key 中转。补丁里的 `baseURL` 改指中转，Harness 子进程环境变量里的“Key”换成每次启动随机生成的令牌，**真 Key 不进 Harness 进程**。
- 中转核对令牌（常量时间比较）后换成真 Key，用项目统一的不跟随跳转的 `net.open_url` 转发到账户主机；上游返回 3xx 时回 502、不转交 Location。只转发账户地址路径（`/anthropic`）下的请求；令牌不对回 401，真 Key 本身也换不来转发。响应按块流式转回（SSE），上游 4xx/5xx 的报错原样给 Harness 显示。
- 子进程的 `NO_PROXY` 补上 `127.0.0.1,localhost`，用户设了代理时中转请求也直连。中转和运行时同生共死：换路由、地址或 Key 重启运行时时一起换，令牌也换。DeepSeek 路由不变。
- 验证：容器离线单测 811 项通过、38 项跳过。新增 `KeyRelayTest`（跨主机 307 时目标服务收不到 Key、令牌和路径校验、SSE 流式与令牌换 Key、上游报错透传）；真实 DSH 0.2.1rc2 运行时（Linux 版）回归 `test_minimax_cross_host_redirect_does_not_leak_key` 按 Codex 的步骤复现（A 307 到 localhost:B）：B 一次都没收到请求，任务不算完成；临时去掉中转时同一用例失败（B 收到 Key），确认用例能抓住这个问题。原有 MiniMax 两路由 × 两模型 × 两轮回归改为经中转运行，仍全部通过。
- 没验证：Windows 上的真实运行时回归与桌宠 Smoke Test（容器以 root 运行，QtWebEngine 起不来）；低完整性进程连回环口在 Windows 实机上的表现；真实 MiniMax 服务。

## 2026-11-11：PR #32 同步基线与文档冲突修复（Codex）

- PR #31 已合入；在独立工作区将 `collaboration-baseline` 的 `4f9ed763d97320a508470ae6d266af83c99b853e` 同步到 PR #32，保留 MiniMax 路由与 Key 中转修复、图库 R1～R5 和识图诊断，以及计划书“回复带语音 / 陪伴日期”的现有内容。
- 唯一需要手动解决的冲突是本文件：先保留图库后续修复记录，再保留 MiniMax 接入及跳转 Key 修复记录；测试手册自动合并，双方条目均保留。没有手动改运行代码或测试逻辑。
- 核对：MiniMax 的 agent / router / 模型页及相关测试与原 PR #32 `7d7b1e5` 一致；图库运行代码及相关测试与同步基线一致；无未解决条目或冲突标记，`git diff --check` 通过。
- 已启动的整合检查已完成：独立合成用户目录运行 `tools/run_checks.bat` 的相同检查（临时批处理仅替换解释器绝对路径），819 项中 812 通过、7 跳过；Windows 桌宠 Smoke `passed: true`、`failed_checks: []`，退出码 0。记录见 `output/PR32-conflict-fix-2026-11-11/`。后续只补充本文档与任务状态，未为文档再次运行全套测试。
- 未验证真实 MiniMax 账号、费用、延迟与真实 QQ；没有读取真实 `.env` 或数据库。冲突修复提交仍由 Claude 复核，PR 由负责人合并。

## 2026-11-11：界面翻新与应用图标（Claude）

负责人要求：界面太繁杂需要翻新；参考计划书 v4 为后续功能留空间；聊天面板里让她办事可能需要上传文件，纳入后续计划；能整合的设置整合；引导要清楚；使用 `data/icons` 下的新图标。

- **图标**：把 Codex 生成的鲸娘图标复制到 `src/aicompanion/assets/icons/`（.ico 含 16～256 像素各档，另存 256 像素 PNG），新增 `ui/icons.py`。应用、聊天面板、托盘、设置窗口都用它；Windows 上声明自己的 AppUserModelID，任务栏不再显示 Python 图标；`tools/build_windows.py` 打包时把它设为 exe 图标；`pyproject.toml` 加入包数据。
- **设置窗口**：新增 `ui/settings_layout.py`（分组侧栏 `SettingsNav`、页面 `Page`、卡片 `Section`、“即将推出”占位 `ComingSoon`）。原来 14 个平铺标签整合为 12 页、4 组：
  - 概览：准备情况清单，可一键跳到对应页；
  - 她：角色与形象、关系与心情、记忆与心事（心事并入）、主动与提醒（主动聊天和免打扰从语音页移出，与提醒合并）；
  - 能力：模型与 Key、声音与通话（分成“她怎么说 / 语气与风格 / 你怎么说”三张卡）、办事（操作日志并入）、表情图库；
  - 连接：QQ 与社区（社区与黑话并入）；
  - 系统：存储位置、用量。

  每页有标题和一句说明，计划书 v4 的后续功能放了“即将推出”卡片。底部固定“取消 / 保存”。支持 `page=` 直接打开某一页。原有页面类没有改动，控件属性名保持不变，保存逻辑不变。语音页里重复的“模型”“千问合成”只读行已删除。
- **聊天面板**：
  - 顶栏改为名字和模型、状态、角色切换，以及“朗读”“设置”两个开关；
  - 聊天模型没配置时，顶部显示引导条，可一键打开“模型与 Key”页；
  - 输入栏新增“办事”开关：发出的这一句交给她办事，发完自动关闭；
  - 新增“＋”附件按钮占位，暂时灰色；
  - “开启通话 / 结束通话”改为“通话 / 挂断”。
  - 界面里的“模型”页统一改称“模型与 Key”页。
- **计划书 v4**：新增“界面与引导”一节（信息架构表，写明各页预留给哪项功能）；功能七加入“把文件交给她办事”（附件复制到任务目录、原件不动、类型和大小限制、计入上下文预算、主人 QQ 私聊后续同样处理）；阶段四加入聊天面板附件办事。
- **验证**：容器离线单测 811 项通过、40 项跳过，新增 `tests/test_ui_refresh.py` 5 项（图标随包、分组和逐页可达、概览跳转、改组后保存仍写入、办事开关）。桌宠自检新增 `app_icon_ready`、`panel_controls_ready`、`settings_pages_ready`。用离屏截图检查了设置窗口 12 页和聊天面板。
- **没验证**：Windows 实机观感、任务栏图标、高 DPI 缩放；`tools\run_checks.bat`（需 Windows）；打包后 exe 的图标。

### 2026-11-11 补充：Codex PR #33 审计 P2（一次性办事开关）修复（Claude）

- 问题：`_send_current_message` 只在“需要补 `/做` 前缀”时关掉“办事”开关；手写了 `/做` 等前缀时开关一直开着，下一句“谢谢”会变成 `/做 谢谢` 启动任务，任务忙时说“停”也会变成 `/做 停`，绕过停止分支。
- 修复：开关打开时，发出任何一句非空消息都先复位，再决定是否补前缀；停止词（与 `submit` 的 `STOP_WORDS` 同一套，忽略句末感叹号和句号）不补前缀，打开“办事”时说“停”也照样停。
- 验证：`tests/test_ui_refresh.py` 补“手写前缀后开关关闭”“下一句仍为普通聊天”“开关打开时停止词不补前缀”，新增 `test_stop_word_reaches_stop_branch_while_busy`，经生产 `_send_current_message` + `submit` 检查：开着办事发 `/做 整理合成文件` 后开关复位；任务忙时无论开关是否打开，“停”都进入 `_stop_agent`，没有启动新任务。两项用例在修复前的代码上失败，修复后通过。容器全套单测 825 项：787 通过、38 跳过（Windows 专用用例在 Linux 上跳过）。
- 同时合并 #32 最新提交 `1b4be36`（Codex 的基线同步），解决本文件和 `docs/tasks.md` 的文档冲突，双方记录都保留。
- 没验证：Windows 上的 `tools\run_checks.bat` 与桌宠 Smoke Test。

## 2026-11-11：V4-2 人格包（Claude）

计划书 v4 功能二，阶段二第一项。设计见 `docs/tasks/V4-2-persona.md`。

- 新增 `domain/persona.py`：人格数据与清洗；提示词附加段（背景、示例、命中的世界书）；导入本项目格式、SillyTavern JSON 和 PNG 角色卡；导出本项目格式和 SillyTavern v2。
- 新增 `services/persona_service.py`：存取、切换、保存（留版本，最多 30 个）、复制、删除（内置人格和正在用的不能删）、回退、导入、首次启用迁移、角色包升级不覆盖用户修改、恢复内置原文。当前人格写回角色表的设定和说话风格。
- schema 8→9，新增 `personas`、`persona_versions` 两张表，不改已有表。旧库照常备份后升级，**不需要删库**。
- `characters/registry.py`：启动时给每个角色补齐人格；已经启用人格的角色，角色包升级改由人格服务处理（旧的“直接改角色表”逻辑只在第一次启用前运行一次）。
- `services/context_service.py`：聊天提示词加入当前人格的背景、说话示例和命中的世界书。
- 新增 `ui/persona_page.py`（人格编辑器），放进 设置 → 角色与形象；保存设置时先保存人格，设定和说话风格以当前人格为准。
- 验证：
  - 容器离线全量测试通过（含新增 `tests/test_persona.py` 14 项）；
  - 桌宠自检新增 `persona_ready`；
  - 离屏截图检查了人格编辑器。
- 没验证：
  - Windows 实机；
  - 真实模型下切换人格后语气变化是否明显（用“预览她会怎么说”和实际聊天试）；
  - 真实 SillyTavern 角色卡（只用了按规范构造的合成卡片）。

### 2026-11-11 补充：Codex PR #35 审计 R1～R3 修复（Claude）

- **R1 预览绕过额度**：“预览她会怎么说”发请求前按陪伴用途检查日金额额度（复用 `usage.meter.Quota`），到限时显示原因、不发请求。没有新增计费或预扣。
- **R2 导入丢草稿**：先读取并校验卡片；读不进来时直接提示，不动草稿。读进来后，如果编辑器里有没保存的改动，弹出“保存 / 放弃 / 取消”：取消就不导入，草稿和当前选择保持不变；保存失败也不导入。
- **R3 PNG 解压无上限**：`zTXt` 和压缩 `iTXt` 改为有上限的解压（`decompressobj` 最多解出上限 +1 字节就停）。解压后的文本、chara / ccv3 各块的累计量都不超过 `MAX_CARD_TEXT`（2 MB），超限报 `PersonaError` 提示用户。base64 解码后的 JSON 更小，所以 JSON 解析的输入也有上限。
- 同步了 `collaboration-baseline`（#32、#33 已合入），解决了 `implementation_status.md` 和 `tasks.md` 的文档冲突。
- 验证：`tests/test_persona.py` 新增 3 项，修复前的代码上都失败、修复后通过：
  - 压缩 zTXt / iTXt 卡片正常导入；约 8 MB 的压缩炸弹（文件小于 64 KB）被拒绝；两块单独都不超、合计超限也被拒绝；不再调用整段解压的 `zlib.decompress`。
  - 导入时有草稿：取消、读不进来、保存、放弃四种情况。
  - 额度已用完时预览不发请求；调高额度后照常预览。
  - 容器全量单测 842 项通过（其中 38 项跳过）。
- 没验证：Windows 上的 `tools\run_checks.bat` 与桌宠 Smoke Test；导入解析仍在界面线程执行，现在有上限，最坏情况是解析约 2 MB 文本。

### 2026-11-11 补充：PR #35 复审的非阻塞建议——换人格后旧预览清掉（Claude）

- 问题：切换或选中另一个人格后，上一个人格的预览文字还留在界面上，要再点一次预览才刷新，容易误以为是当前人格说的。
- 改动：
  - 编辑器换到别的人格时清空预览。
  - 预览结果写明是哪个人格说的，例如“鲸娘（工作模式）：……”。
  - 每次预览带一个序号：结果回来前换了人格或又点了预览，旧结果直接丢掉，不显示。
  - 同一个人格保存后，预览保留。
- 验证：新增 `test_preview_is_labeled_and_cleared_when_persona_changes`，修复前失败、修复后通过；容器全量单测通过。
- 没验证：Windows 实机观感。

## 2026-11-11：默认角色改名“鲸鲸”（Claude）

负责人要求把角色名字改成“鲸鲸”。只改她的名字；二创形象“鲸娘”（美术、立绘、文档里对这个设计的称呼）不改。按长期约束 1，名字属于角色身份，不属于人格。

- **角色包**：`character.json` 的 `name` 改为“鲸鲸”，新增 `previous_names: ["鲸娘"]`。内置人格升到 v6：开头改成“你是‘鲸鲸’……（这个形象原本叫‘鲸娘’，现在你叫鲸鲸）”，其余文字和说话风格与 v5 相同；v5 原文移到 `previous_*`，v2～v4 留在 `older`。QQ 聊天卡的名字和风格说明同步改。
- **已有数据库**：`CharacterRegistry._rename` 在启动时检查，库里的名字还是包的旧名字（鲸娘）就改成鲸鲸。每个新名字只改一次（记在 `pack_name:<角色>`）：之后用户在设置里改回“鲸娘”不会再被改掉；用户自己起的名字不在旧名单里，不动。
- **人设**：已经启用人格包的库，内置人格没改过的，按 V4-2 的包升级机制自动换成 v6，并留一个版本；改过的不覆盖，编辑器会提示“内置版本有更新”。还没启用人格的老库，按原来的 `previous_description` 规则升级。
- **QQ / 意图识别**：别人叫“鲸鲸”也能认出是在说她；“鲸娘”作为旧名继续认（`bridge/service.py` 的名字集合、`ABOUT_HER`、`HER_NAMES`，`relationship/intent.py` 的 `HER`）。心跳接口没有角色时的默认名改为鲸鲸。
- 自检脚本里的提示词同步改为鲸鲸（`tools/check_dsh.py`、`tools/test_dsh_live.py`）。
- 验证：
  - `tests/test_persona_upgrade.py` 改为 v6，覆盖从 v2～v5 原文升级；新增两项：改名只改一次、用户改回旧名和自起的名字都不动；已启用人格的库从 v5 鲸娘升到 v6 鲸鲸。
  - 更新了断言旧名字的 3 处测试（聊天卡两处、QQ 心跳一处）。
  - 容器全量单测 845 项通过（其中 38 项跳过）。
- 没验证：Windows 实机；负责人现在的库里用的是哪个人格。如果当前人格是“我改过的版本”，或者内置人格被改过，名字会变成鲸鲸，但人设文字里仍是“你是‘鲸娘’”，需要在人格编辑器里手动改，或者点“恢复内置原文”。

## 2026-11-11：V4-3 持续情绪（Claude）

计划书 v4 功能三。设计见 `docs/tasks/V4-3-mood.md`。

- 新增：
  - `domain/mood.py`：四种情绪的强度、半衰期回落、事件效果、提示词说明；
  - `services/mood_service.py`：读写、事件来源、原因、手动哄好；
  - `ui/mood_card.py`：设置页的心情卡片。
- `services/emotion_service.py`：不再每句话把心情重置为平静。保留孤独感、精力两个旧数值；用户消息交给心情服务做兜底和久别判断。
- `controllers/chat_controller.py`：同一轮的 `<mind/>` 推动心情。
- `ui/agent_session.py`：办事完成时记一次“一起做成了事”。
- `services/context_service.py`：“当前心情”改为程度、原因和表现要求，并写明求助、办事、安全照常。
- `ui/main_window.py`：开始一轮对话前，先让心情按时间回落再读取。
- schema 10→11，新增 `mood_state`、`mood_events`。
- 验证：
  - 容器离线全量 874 项通过、41 项跳过。新增 `tests/test_mood.py` 20 项，覆盖：回落、道歉按比例消气、人格倾向、`<mind/>` 换算（转述和针对别人不算、群聊打折）、兜底词、久别、心情不改好感、哄好、提示词、跨实例持续、不再每句重置、心情卡片。
  - 桌宠自检新增 `mood_ready`。
- 没验证：
  - Windows 实机；
  - 真实模型下生气、委屈、撒娇的表现是否自然、是否过度；
  - 半衰期、力度这些默认值的体感。

## 2026-11-11：PR #38 的范围更正，以及 Codex 第一轮审查的四项修复（Claude）

**范围更正**：PR #38 并非只有持续情绪。它的 base 是 `01e6651`（schema 9），在此之上同时新增了**记忆召回基础与整份请求预算**（计划书 v4 功能六的阶段二部分，任务编号 V4-6a）和**持续情绪**（V4-3）。之前说“召回和预算已合入”是错的。

- **记忆召回**（`services/recall.py`、`storage/fts.py`）：
  - 能想起存档里的旧事；
  - 能找回最近 30 条之外的更早聊天：同频道，不越过删除水位线，用 FTS5 trigram 全文索引，两个字的词用 LIKE 补查；
  - 能想起以前办过的事，只在私下用。
- **整份请求预算**（`services/budget.py`）：人格、记忆、召回、关系、表情候选、办事说明、历史按用途合计；超了从最早的历史开始裁；每轮的明细存进 `context_report:<角色>`。
- **迁移**：9→10 建全文索引，旧库升级时补建；10→11 建 `mood_state`、`mood_events`。旧表都不改，升级前照常自动备份。

**Codex 审查（`d25e9da`）四项的修复**：

1. **[P1] 私下办事内容经心情原因进了群聊**：`MoodService.reason / prompt` 新增 `public` 参数。公开场合（群聊、陌生人私聊）只说笼统的事件类型（例如“一起做成了事”），不说具体原因；心情强度仍然共享。`ContextService` 按场景传参。
2. **[P1] 删除的事实经旧办事记录召回**：做不到逐条判断旧任务里有没有提到被删的内容（被删事实的原文已经没有了），所以采用保守过滤：用户删过记忆后，删除时刻之前的办事记录不再参与召回，和聊天记录的删除水位线一致。办事记录本身保留，供审计；删除之后的新任务照常可以想起。
3. **[P2] 用词兜底先提交，撤不回**：用户消息的用词兜底改为“候选”，等这一轮结束时再决定：
   - 她的 `<mind/>` 有明确判断（包括“这是转述 / 在说别人”）时，以判断为准，丢掉兜底；
   - 没有判断时才用兜底。

   另外：前面带“不 / 没 / 别 / 非”的不算（“不开心”不算开心，会归为担心）；看起来是转述或引用时，不记负面事件。没有新增模型请求。
4. **[P2] 已知窗口超限仍然发送**：
   - 新增硬上限，等于模型窗口减去输出预留。裁完历史后，如果人格、当前这句话等不能删的部分仍超过硬上限，这一轮不调用模型，抛出 `BudgetExceeded`，并告诉用户怎么办（精简人格或世界书、说短一点、换窗口更大的模型）。人格和当前要求不会被悄悄删掉。
   - 软预算在输出预留吃光窗口时为 0，不再退回 20000 字的默认值。
   - 窗口未知时行为不变。

- 测试：新增 `tests/test_pr38_fixes.py` 共 9 项，按 Codex 的合成用例覆盖：群聊提示词不含私下任务代号；删除后不经旧任务召回、审计记录保留、新任务照常召回；连续四轮转述不累积生气；“我今天不开心”不变开心；没有判断时兜底仍然生效且不重复叠加；预算上下限；超限时模型调用 0 次、报告标记“没有发送”、人格不变；窗口未知时照常发送。`tests/test_mood.py` 按新的兜底时机调整了两项断言。
- 容器离线全量 883 项通过、41 项跳过。
- 没验证：Windows 实机，以及 `tools\run_checks.bat`、Smoke。

**Codex 第二轮（`fbd5dfd`）的两项修复，以及两项非阻塞的记录**：

1. **[P1] 心情原因绕过删除边界**：`MoodService.reason` 也遵守记忆删除的时间水位。删除时刻之前（同一秒也算）记下的具体原因不再交给模型，只给笼统的事件类型；事件记录和心情强度都保留。删除之后的新原因照常可以具体说。
2. **[P2] 中性判断仍走兜底**：`on_mind` 把“有判断、结论是中性”（at / voice / tone / toward / feel 任一有值，但不需要改心情）与“没有判断”区分开，前者返回 `'neutral'`，不再走用词兜底；只写了 intent / lookup 的才算没有判断。没有新增正则。
- **非阻塞·界面**：心情卡片的“哄好她 / 刷新”移到卡片最上面靠左。在 980 宽的窗口里，即使下面的关系表格把整页撑宽，也不用横向滚动就能看到。
- **非阻塞·范围说明**：整份请求预算目前只接入 `ChatController.generate`，也就是自然聊天、主动发言、QQ 回复这条链路。识图（`bridge/media.py`）、后台分析（记忆整理、关系分析、社区学习）和办事（Harness）**还没有**走这个统一预算入口，仍用各自原来的长度限制；计划书里“每种用途有自己的上限”目前只完成了聊天这部分。
- 测试：`tests/test_pr38_fixes.py` 新增 `SecondRoundTests` 2 项，按 Codex 的生产链路复现：任务完成写原因 → 删除事实 → 私下提示词里没有代号、心情和事件记录保留；连续四轮中性判断，生气为 0，不记兜底事件。容器离线全量 885 项通过、41 项跳过。另外 `test_recall`、`test_pr38_fixes` 的 tearDown 会等后台关系分析线程写完再删除临时目录，修掉偶发的“目录不为空”错误（只改测试）。

**Codex 第三轮（`debf37c` 实机）**：用户说“你不是笨蛋，我是在给你打气啦。今天终于把项目做完了！”，她这一轮没有写 `<mind/>`，用词兜底按“笨蛋”记了一次“被阴阳”（生气 8.4、委屈 5.6）。改成保守做法：没有她自己的判断时，兜底只认道歉、喜欢、夸奖、担心这几类不会误伤的词，**负面意图一律不靠用词推断**；被损、被骂只由她当轮的 `<mind/>` 判断记录。没有增加正则，也没有增加模型请求。新增无 `<mind/>` 的 controller 回归 `ThirdRoundTests`。

另外登记了一个基线问题，留给 V4-8 优先修（本 PR 没有改相关代码）：已经 complete 并展示完的回复，会被下一轮 submit 或退出时的 `_end_call → interrupt` 改成 cancelled，重启后显示“被打断”，全文索引也会漏掉这些消息。

## 2026-11-11：V4-8 回复带语音·第一版（Claude）

计划书 v4 功能八。范围、取舍规则和没做的部分见 `docs/tasks/V4-8-voice.md`。

- **基线修复**：已经结束的回复不会再被之后的 `interrupt` 改成 cancelled；已完成的回复按全文记入上下文；旧的 cancelled 记录不批量修改。
- **默认开启**：朗读默认开启，说话方式默认“智能朗读”。手动关过朗读的只提示一次。
- **智能朗读**：新增 `speech/smart.py`，按规则取舍、分段。主窗口逐句交给它；办事结果也用它。
- **失败回退**：`SpeechOutput` 云端合成失败时，这一轮剩下的句子改用系统朗读，回复照常完成；同一轮两段之间停 250 毫秒。
- 设置页增加“智能朗读”选项，并更新了说明文字。用量里新增每轮念的字数。
- 测试：新增 `tests/test_voice_reply.py`，覆盖：
  - 短回复全念、第一段只放一句；
  - 约 800 字的办事结果念出来不超过约 360 字，且包含结论、要你确认的事和结尾情绪；
  - 清单、表格、代码、路径不照念；
  - 重复句只念一次；
  - 云端失败只把这一轮改成本地朗读，回复不中断，这一轮后面的句子直接本地念；
  - 日语不回退；
  - 已完成的回复经 `interrupt` 后仍是 complete 并保留全文，生成中被打断的照常 cancelled，旧 cancelled 不改；
  - 默认说话方式为智能朗读。

  桌宠自检新增 `speech_default_on`。
- 容器离线全量 897 项通过、41 项跳过（worktree 里用 `PYTHONPATH=src` 运行）。
- 没验证：
  - Windows 实机听感、首次出声延迟；
  - 系统朗读回退的真实效果；
  - 云端声线（尚未配置）。

## 2026-11-11 群聊回复被截断、前后对不上（task/v4-qq-group-fix，基于 PR #38）

- 做了什么：按态度截条数只丢零碎话，有内容的回答和吵架还嘴全发；列举的一行不按空格拆；不再按条数防刷屏合并（拆几条发几条，按打字速度慢慢发）；截掉零碎话时数据库记录改成实际发出的内容；冷淡 / 不熟提示加“请教、吵架时把话说完”的例外。桌面提示词带"QQ 群里最近的动静"（单向，群里看不到桌面）。插件 0.9.6（不再合并段）。说明见 `docs/tasks/V4-qq-group-fix.md`。
- 依据：本机库 qq 群聊记录（只读查看，未提交任何库内容）：配队回答在群里只显示前三条，记录里却是整段。
- 怎么验证：`PYTHONPATH=src:tests QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests` → 891 项通过（跳过 41）；用真机那条配队回复回放：原来只发出“行 / 这次给完整一套 / 而且八个角色不重复”，现在 8 条全发、名字列表整行一条。
- 没验证：Windows 真机 QQ 群实际发送（插件需重载为 0.9.6）；推理过长导致的慢回复未处理。

## 2026-11-11 PR #40 文档冲突修复（Codex）

- 将已合并 PR #39 的基线 `30a862f` 接入 PR #40，保留原有提交历史；冲突只在本文件末尾的追加记录。
- 保留 V4-8 语音第一版和群聊回复修复两段原始记录，未删改双方的功能说明、测试记录或未验证事项。
- 手工修改仅涉及 Markdown；运行代码、测试和插件文件按 Git 的自动合并结果保留，没有手工改动。
- 验证：检查双方原始记录完整保留、无冲突标记、差异无空白错误，非文档文件与自动合并树一致。
- 按负责人要求，本次纯文档冲突修复不运行回归或真实模型／QQ 测试；不改变 PR #40 功能补丁的审查与验收状态。

## 2026-11-11 V4-8b 鲸鲸默认声线（task/v4-8b-whale-voice，叠在 #39 之上）

- 做了什么：
  - 角色声线设定 `domain/voice_profile.py`；
  - MiniMax 语音客户端；
  - 模型页没配语音时，鲸鲸用 MiniMax 的少女音色作默认声线，和聊天共用 MiniMax 账户；
  - 心情明显时指定情绪，其余交给模型按台词自动选；
  - 第一次自动把朗读方式切到云端声线，之后尊重用户选择；
  - 设置里可以从 6 个候选音色里换、试听。

  说明见 `docs/tasks/V4-8b-whale-voice.md`。
- 怎么验证：`PYTHONPATH=src:tests QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests` 全部通过（跳过 41）。新增 `tests/test_whale_voice.py` 覆盖以下内容：
  - 请求体；
  - hex 解码；
  - 错误不泄露 Key；
  - 按服务种类分流；
  - 默认声线路由、候选切换、模型页显式配置优先、离线不套用。
- 没验证：
  - 真实 MiniMax 语音接口；
  - 音色听感和情绪效果（需负责人试听拍板）；
  - Windows 真机。

## 2026-11-11 PR #41 文档冲突修复（Codex）

- 将最新协作基线 `151b4d2` 接入 PR #41，保留已有提交历史；唯一冲突为本文件末尾的追加记录。
- 完整保留基线的群聊修复记录与本 PR 的 MiniMax 默认声线记录，未覆盖双方已有功能、测试或未验证说明。
- 手工冲突修复仅涉及 Markdown，运行代码、插件和测试按 Git 自动合并结果保留。
- 冲突修复检查双方原记录完整、无冲突标记、差异无空白错误；纯文档处理不单独运行回归。功能审计及其测试结果在 PR 评论另行记录。

## 2026-11-11 PR #41 Codex 审查三项 P2 修复（Claude）

- 做了什么：
  - **审查 1（FixedRouter 缺接口，离线 Smoke 起不来）**：`FixedRouter` 补上 `tts_target` / `default_voice`，返回 None（固定解析器不管语音合成，界面退回本地朗读）；首次接入的判断挪到 `domain/voice_profile.adopt_default_voice`，router 没有默认声线能力时安全跳过。
  - **审查 2（首次接入覆盖明确的本地朗读）**：只有库里从没存过 `tts_provider` 且环境 `TTS_PROVIDER` 是默认 system 时才切到云端声线。旧库里存着的 system 无法区分是默认还是用户选的，一律按用户选择保留，要用新声线由用户自己在设置里切。另外设置页保存时，如果库里没存过、用户也没动朗读方式，就不再把默认值写进去，避免“没选过”被存成“明确选了本地朗读”。
  - **审查 3（试听提前保存音色，取消撤销不了）**：试听不再写 `voice_choice:<角色>`；`router.tts_target(角色, voice_choice=候选)` 临时取一份合成配置，经 `SpeechOutput.enqueue(..., target=)` 只交给这一句试听。只在点“保存”时写库。
- 怎么验证：容器内 `QT_QPA_PLATFORM=offscreen python -m unittest discover`（tests 目录）**923 项通过、8 项跳过**。新增 `tests/test_whale_voice_review.py`（9 项）：
  - FixedRouter 接口对齐；用 FixedRouter 调生产 `MainWindow._apply_settings` 不报错；
  - 库里明确存 system（及 qwen / cloud）+ 配了 MiniMax 账户 → 生产 `_apply_settings` 后仍是原选择；新库 + MiniMax → 用上默认声线，只切一次；环境指定别的朗读方式时不切；
  - 设置窗口：A → 选 B → 试听（入队的确是 B）→ 取消 → 库和路由仍是 A；再选 B → 保存 → 才是 B；
  - 没动朗读方式时保存其他设置不写入 `tts_provider`，改过的照常保存。
  - 把 `src/` 改动撤回后，这些用例 7 项失败 / 报错（含 `AttributeError: 'FixedRouter' object has no attribute 'default_voice'`），确认能复现审查问题。
- 没验证：
  - **Windows Smoke（`tools\run_checks.bat`）没在真机跑**：容器没有音频设备，`tools/smoke_pet.py` 在 Qt 多媒体初始化处段错误，与本修复无关；需要在 Windows 上重跑确认 `"passed": true`。
  - 真实 MiniMax 调用、音色听感照旧未验证。

## 2026-11-11 PR #41 Codex 复核 P2 修复（Claude）

- 做了什么：设置页判断“用户是否选过朗读方式”改为记录下拉框在窗口初始化后有没有被操作（`tts_provider_touched`），不再只比较首尾值。切到千问又选回本地朗读，同样算明确选择，保存时写入 `tts_provider=system`。完全没动时仍不写入，新库照常可以自动接入默认声线。顺手去掉 `docs/tasks/V4-8b-whale-voice.md` 第 18 行行尾空白。
- 怎么验证：`tests/test_whale_voice_review.py` 新增 3 项，都调用生产 `SettingsWindow.save` 和生产 `MainWindow._apply_settings`：
  - 窗口打开期间添加 MiniMax 账户，千问 → 本地朗读 → 保存：库和运行时都是 system，没有自动接入标记；
  - 先选回本地朗读并保存，之后才添加账户：同上；
  - 完全没动就保存，之后添加账户：仍自动接入默认声线。
  前两项在 `0950457` 上失败，修复后通过。容器内全量 `unittest discover`：**926 项通过、8 项跳过**；`git diff --check` 无输出。
- 没验证：本提交没有在 Windows 上重跑 `tools\run_checks.bat`（上一提交 `0950457` 的 Windows 检查 Codex 已跑通：916 通过、7 跳过，Smoke `"passed": true`）。真实 MiniMax 调用、音色听感照旧未验证。

## 2026-11-11 V4-2b 关系设定（task/v4-2b-bond，叠在 #40 之上）

- 做了什么：
  - 她和你的关系变成人格包的一项（预设：女儿 / 女朋友 / 妹妹 / 好朋友 / 自定义）。
  - 桌面和 QQ 的提示词、群聊本人标记、外人冒充检测、聊天卡、办事人设都按当前关系生成，不再写死“爹”。
  - 记忆里统一写“用户”。
  - 内置鲸鲸升到人格第 7 版，关系挪进 bond，默认仍是女儿，行为不变。
  - 人格编辑页可以改关系。

  说明见 `docs/tasks/V4-2b-bond.md`。
- 怎么验证：全部单元测试通过（跳过 41 项）。新增 `tests/test_bond.py`（走真实桥接）和编辑页测试。
- 没验证：真实模型下不同关系的说话效果；Windows 真机。

## 2026-11-11 PR #42 文档冲突修复（Codex）

- 先接入 GitHub 当前目标分支 `task/v4-8b-whale-voice` 的 `0950457`，处理期间该分支更新到 `837a2f1`，已继续接入最新提交，保留双方提交历史；没有合并远程 PR，也没有修改目标分支。
- 保留 #40、#41 的实施及审查修复记录，以及 #42 的关系设定记录；任务表采用目标分支较新的语音任务状态，并保留关系设定任务。
- 手工冲突解决仅涉及本文件和 `docs/tasks.md`；非 Markdown 文件与 Git 自动合并树一致，没有手工修改运行代码。
- 文本检查覆盖双方记录保留、无残留冲突标记和差异空白检查。按负责人要求，纯文档冲突不单独运行回归；本 PR 的功能审计与验证结果另行记录，不将文档处理等同于功能通过。

## 2026-11-11 PR #42 Codex 审查 P2 修复（Claude）

- 做了什么：
  - **SillyTavern 导出再导入保留关系**：`export_sillytavern` 在 `extensions.aicompanion` 里写入完整 bond，`_from_sillytavern` 读回。别处的卡片没有这项时不写 bond，导入后仍按默认关系。
  - **回退旧快照不再留下新关系**：`PersonaService.revert` 恢复的是整份历史快照，快照里没有 bond（升级前的旧库）时按默认关系（父女）恢复。普通编辑 `with_content` 缺字段不改的语义不变。
  - 同类问题顺手修：`restore_builtin`（恢复内置原文）也是整份覆盖，角色包没写 bond 时回到默认关系，不再保留用户后来改的关系。
- 怎么验证：`tests/test_persona.py` 新增 3 项：自定义 bond（女朋友 / 宝 / 自定义标记和相处方式）的 SillyTavern 往返；无 bond 旧快照回退（含落库读回、局部编辑仍保留关系）；无 bond 角色包恢复内置原文。3 项在 `609e7be` 上失败，修复后通过。容器内（Linux、Python 3.13、Qt offscreen）全量 `unittest discover`：**935 项，927 通过、8 跳过**。
- 没验证：本提交没有在 Windows 上重跑 `tools\run_checks.bat` 和桌宠 Smoke；不同关系在真实模型下的说话效果仍未验证。

## 2026-11-11 V4-9 陪伴日期基础版（task/v4-9-companion-dates，叠在 #42 之上，schema 11→12）

- 做了什么：
  - 认识第几天：按日历日算，见面当天是第 1 天；同时统计聊过几天。
  - 纪念日：第 100 天这类整数天、周年（默认也是她的生日）、办成事的周年、公历和农历节日。
  - 用户自己加的生日和纪念日，可以按农历记。新依赖 `lunar_python==1.4.9`（MIT，负责人同意引入），Windows 上要重新 `pip install -e .[agent]`。
  - 私下聊天时当天提一句，一天只提一次，重启后也不重复；群聊里不带主人的日子。
  - 设置里新增“我们的日子”。

  说明见 `docs/tasks/V4-9-companion-dates.md`。
- 怎么验证：全部单元测试通过（908 项，跳过 41 项）。新增 `tests/test_companion_dates.py`，用测试时钟覆盖第 100 天、跨午夜、周年、生日、节日、只提一次、群里不带、换人格天数不变、设置卡片。
- 没验证：
  - 提前提醒（等调度器）；
  - 真实模型的提起效果；
  - Windows 真机。

## 2026-11-11 PR #43 冲突处理及 Codex 首审

- 合入当前目标分支 #42 的最新提交 `609e7be`，保留 #40～#43 的任务和实施记录；手工冲突只涉及 `docs/tasks.md` 和本文件，运行代码使用 Git 自动合并结果；另在任务说明记录本轮审查问题。没有合并远程 PR，也没有改目标分支。
- `tools\run_checks.bat` 退出码 0：946 项单元测试（939 通过 / 7 跳过），Windows 桌宠 Smoke `failed_checks=[]`、`passed=true`、退出码 0；日期原专项 14 项通过。
- 补充临时合成复现确认 4 项 P2：纪念日开关未关周年；一次性农历闰月日被改到普通月；首次办成事按开始日取周年；桌面与绑定用户的 QQ 私聊同时生成会重复提醒。详细触发条件见 `docs/tasks/V4-9-companion-dates.md`；未将失败复现加入正式测试集。审查未通过，待 Claude 修复，不能标记可合并。
- 本轮时区疑点已复核剔除：聊天时间保存为 UTC，原复现的输入假设错误，不计入问题。
- 未验证：真实模型话术、真实 QQ 发送、用户真机交互和长期体验；本轮验证只用临时合成库及假模型，不访问真实配置或用户数据库。

## 2026-11-11 PR #43 第二轮审计与 #42 依赖同步（Codex）

- 接入 #42 最新修复 `14d9cb6`，再次保留双方任务和实施记录解决两份 Markdown 冲突；运行代码保持 Git 自动合并结果，未手工改实现，未强推或合并远端 PR。
- 审计新增 R5（农历重复日忽略起始年份、提前一年提醒）、R6（提醒开关重置生日草稿、保存为今天）两项 P2；首审 R1～R4 复现仍失败，累计 6 项 P2 待实现方修复。详情见 `docs/tasks/V4-9-companion-dates.md`，当前不可标记可合并。
- 重新运行 `tools\run_checks.bat` 退出码 0：949 项单元测试（942 通过 / 7 跳过，180.648 秒），Windows 桌宠 Smoke `failed_checks=[]`、`passed=true`、退出码 0。新补充合成验证 5 项（3 通过 / 2 失败，失败对应 R5、R6）；首审 4 项缺陷复现均再次触发。失败复现保留在本机忽略目录，没有加入正式测试集。
- 未验证：真实模型话术、真实 QQ 发送、用户真机交互及长期体验。本轮只操作临时合成库及假模型，不访问真实配置或用户数据库；不将原检查通过写成已完成阶段验收。

## 2026-11-11 Claude 修复 Codex 两轮审计 R1～R6（PR #43）

- **R1 纪念日开关**：认识周年（含默认的“她的生日”）、第一次办成事周年都跟“纪念日”开关走；用户自己加的她的生日不受影响。
- **R2 一次性农历日期**：不勾“每年”时一律按选中的公历日期存，不再换成农历月日（换了会丢闰月）。每年重复的农历日子仍按同名普通月过。界面提示同步改了。
- **R3 第一次办成事**：按完成时间 `COALESCE(NULLIF(ended_at,''), started_at)` 取最早的一条；旧记录没有完成时间时退回开始时间。
- **R4 同时生成重复提醒**：去掉 `ContextService.pending_dates` 共用字段。拼提示词时按（数据库、角色、key、日期）在进程内加锁占位，占位绑定这一轮的 `turn_id`，查“今天提过没有”和占位在同一把锁里；`ChatController.generate` 结束时成功就 `confirm`（先写库再放锁），失败 / 取消 / 回复被拦下就 `release`。占位 10 分钟过期，防止意外没走到结束。没有轮次 id 的 `context.build()` 只带背景、不带提醒。
- **R5 农历重复日的起始年份**：每年重复的农历日子，要等到它对应的农历年才开始提（和公历分支一致）。
- **R6 日期卡片**：`refresh()` 只在打开卡片、改完认识那天、添加完以后重置对应的日期框；切换提醒开关、删除日子不再把还没提交的日期改回今天。
- 新增测试（`tests/test_companion_dates.py`，14→22 项方法，跑两套时钟共 28 项）：开关覆盖周年、完成日跨午夜和旧记录、一次性闰月日、农历起始年、桌面生成到一半时主人 QQ 私聊不再带提醒、取消的一轮放掉提醒、无轮次只带背景、切开关不丢填写的日期。
- 怎么验证：日期专项 28 项、Codex 两轮 9 个复现全过；相关 11 个模块 207 项在 Linux 虚拟机通过（只缺 Windows 专用的 webrtcvad）。
- 没验证：Windows `tools\run_checks.bat` 与桌宠 Smoke；真实模型话术；QQ 真实发送；真机点击。等 Codex 复核。

## 2026-11-11 PR #43 Codex 第三轮复核通过

- 复核作者 `87c6a5f`，R1～R6 已修复；前两轮 9 项独立复现均通过。本轮未发现新的 P1 / P2，审查通过；仅更新任务和审查记录，没有修改业务代码。
- Windows `tools\run_checks.bat` 返回 0：963 项单元测试（956 通过 / 7 跳过，179.921 秒），Windows 桌宠 Smoke `failed_checks=[]`、`passed=true`、退出码 0。另外 7 项占位生命周期与并发合成验证全部通过（模型失败、预算拒绝、过滤、取消、双线程认领、跨午夜确认）。
- 作者已报告本地 Linux 日期专项及旧复现通过，Windows 检查由 Codex 本轮补齐。具体范围与命令见 `docs/tasks/V4-9-companion-dates.md`。
- 未验证：真实模型纪念日话术、真实 QQ 发送、用户真机点击和长期体验；不标记阶段验收完成。负责人最终合并，本轮未合并 PR；验证只用临时合成库及假模型，不访问真实配置或用户数据库。

## 2026-11-11 PR #43 Windows 界面核心流程真机走查（Codex）

- 负责人指定先验收 Windows 界面。基于 `5619094`，用真实 Qt 主窗口和设置窗口实际点击、键入、滚动走查；专属临时合成库与模拟模型，不加载真实配置或用户数据，不联网或发送真实 QQ。
- 10 项核心流程通过：日期卡入口与展示、修改认识日、生日输入与切开关保留、纪念日开关、一次性农历组合、打断、重试及成功后去重、同库重启恢复、重启当天去重、删除本轮创建的合成日期。截图和详细结果见 `docs/tasks/V4-9-companion-dates.md`。
- 发现 1 项 P3：默认 980×700 设置窗口存在横向溢出，删除按钮要横向滚动才完整可见，左侧说明随后被截断；已有关系页最小宽度会撑宽滚动内容，建议统筹页面与日期行适配。未发现新的 P1 / P2，核心功能走查通过，P3 留作界面改进。
- 仅追加记录和两张不含私人数据的设置截图，业务代码未改；沿用已通过的 963 项单测和 Windows Smoke，不重复全量。临时验收进程已关闭，合成数据与完整证据留在本机忽略目录。
- 未验证：真实模型话术、真实 QQ 发送、连续多日真实使用及整个阶段的负责人长期体验；不写成这些项目均已通过。

## 2026-11-11 开源准备：取名、双语 README 与 MIT 许可（task/oss-readme，Claude）

- 做了什么：项目对外名称定为 **Whalemate · 鲸伴**（包名 `aicompanion` 和仓库名不变）；`README.md` 重写为中文主版本，新增英文版 `README.en.md`，两边互相链接；致谢一节按“运行时依赖 / 设计参考 / AstrBot 社区插件 / 大肥鱼社区”列出计划书和任务说明里参考过的项目，并写明 GPL / AGPL 项目只借鉴思路；新增 `LICENSE`（MIT），README 写明 Live2D Cubism Core、第三方组件和各公司商标不在 MIT 范围内，并加上同人与声音声明。
- 依据：`docs/project_plan_v4.md`、`docs/tasks.md`、`docs/tasks/*.md` 中出现的参考仓库；`src/aicompanion/assets/live2d/LICENSE.md`。
- 怎么验证：只改 Markdown 和新增 LICENSE，不涉及代码，未运行回归；人工核对 README 中引用的本地路径都存在于仓库中。
- 没验证：GitHub 页面上的实际渲染效果；README 里的功能状态按 `docs/tasks.md` 当日状态写，之后需要随任务进展更新。
- 审查修复（Codex PR #44 第一轮，1 项 P1、2 项 P2）：
  - P1：MIT 只覆盖代码和文档。美术素材（`art/`、角色 `sprites/`、`rig/textures/`、`assets/icons/`）的对外许可**尚未由负责人决定**，在中英文 README 里明确排除，并注明保留所有权利；`art/README.md` 补了同样的说明。
  - P2：可选依赖的安装命令改为 `.\.venv\Scripts\python.exe -m pip install ...`，和快速开始用同一个虚拟环境。
  - P2：许可说明补上 Live2D“可扩展应用”的情况：本项目支持导入任意模型，可能属于这一类，发行前需要 Live2D 审核并签订专门协议，小规模豁免不适用。依据是 Live2D 官方说明页；**分类和授权都还没有向 Live2D 确认**。
- 负责人决定（2026-11-11）：美术素材也按 MIT 许可发布。中英文 README 的许可范围改成“代码、文档和美术素材”，`art/README.md` 记录了这项决定；同时写明素材里的公司徽标仍是商标，MIT 不授予商标权。

## 2026-11-12 Live2D Cubism Core 不再随项目分发（task/oss-live2d-core，Claude）

- 做了什么：按负责人要求，把 `src/aicompanion/assets/live2d/vendor/live2dcubismcore.min.js`（Live2D 专有许可）从仓库删除并加入 `.gitignore`；`pyproject.toml` 用 `exclude-package-data` 把它排除在 wheel 之外；`tools/build_windows.py` 默认从构建结果和覆盖更新的发布目录里删掉 Core，新增 `--keep-cubism-core` 只供自用；`tools/fetch_avatar.py` 下载前先打印许可链接。
- 运行时：`avatar/model_package.py` 新增 `cubism_core_available()` / `require_cubism_core()`；设置里选择或手动填写外部模型时，缺少 Core 会弹出获取方法；`AvatarView.set_model` 同样检查；`main_window._apply_avatar` 在缺少 Core 时退回角色包自带的骨骼形象或立绘，状态栏只提示一次。默认角色从来不加载 Core（骨骼形象只用 PixiJS），不受影响。
- 文档：中英文 README 的可选组件和许可说明、`docs/live2d_model_sources.md` 已更新。
- 怎么验证：`PYTHONPATH=src:tests QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests`（容器，Python 3.13）→ 967 项，新增 4 项全部通过；失败 1、错误 2 均为 `test_companion_dates.LunarTests`，原因是容器缺少 `lunar_python`，不改本分支代码时同样失败。
- 没验证：Windows 真机 `tools\run_checks.bat`；真机上缺少 Core 时的提示和退回效果；`build_windows.py` 实际打包；`fetch_avatar.py` 里 Live2D SDK 下载地址（`CubismSdkForWeb-4-r.7.zip`）是否仍然有效。

## 2026-11-12 清理过时文档（task/oss-docs-cleanup，Claude）

- 做了什么：按负责人要求删除 `docs/project_plan.md`、`docs/project_plan_v2.md`、`docs/project_plan_v3.md`、`docs/tasks/T14-smart-expression-original-2026-11-08.md` 和 `docs/tasks/evidence/`（T14 评测证据 11 个文件）。`project_plan_v4.md`、`AGENTS.md`、`T14-smart-expression-design.md` 里的引用改为指向 git 历史（提交 `7ebc39c` 及之前）。本文件中较早的记录保留原样，其中提到的旧计划书同样可在该提交之前的历史中查看。
- 怎么验证：只删文档、改引用，不涉及代码；检查仓库里的 Markdown、Python、JSON 和 bat 文件，除本文件的历史记录和已完成任务的说明外，没有其他地方引用被删的文件。
- 没验证：没有运行回归（纯文档改动）。旧的自绘鲸娘美术不在这次范围内，随 #25（Aboutme 大肥鱼）一起处理。
- 追加（负责人 2026-11-12）：`docs/live2d_model_sources.md` 删除艾米莉娅和 Haru 示例的旧记录，只保留 Cubism Core 获取方式、导入步骤和兼容性说明；Codex 第一轮 P2 指出的 `T14-claude-handoff.md`、`P0-architecture-constraints.md` 引用已补上 `git show 7ebc39c:<原路径>`。

## 2026-11-12 T20：鲸鲸改用本机 Aboutme 动画（PR #25 首轮，提交 `b570e4b`）

- 将协作基线 `fa54555` 合入原分支，保留基线的鲸鲸角色设定及两侧既有任务、实施记录。
- 鲸鲸采用 Aboutme 大肥鱼的 21 个源动作。使用者按上游条款将动画安装到本机，缺包时显示占位形象。中英文 README 新增安装步骤、上游署名及许可范围。
- 此前运行使用的原创鲸娘美术移至 `art/archive/deepseek-whale/`，供创作与查阅。应用图标和已用于表情图库的六张原创图片沿用 MIT 授权。第三方图集和转换产物仅限本机安装。
- 骨骼编辑器和形象包编译器仍供归档源包及自定义角色使用。旧表情库记录在重新扫描前也能按原图哈希找到迁移后的六张图片。
- Windows 运行 `tools\run_checks.bat` 退出 0。1019 项单测用时 179.312 秒，1012 项通过、7 项跳过。使用本机素材的桌宠窗口加载 `AnimationAvatar`；另用 `tools\smoke_pet.py --without-aboutme --output-dir output\pr25-missing-smoke` 从无素材目录启动同一角色，窗口显示 `PlaceholderAvatar`。两份报告均为 `passed=true`，错误、失败项和剩余工作线程均为零。
- Windows 在临时源码目录实际构建 PyInstaller onedir，核对了程序、安装器、MIT 许可、应用图标与六张表情，发布目录中没有本地动画或 Cubism Core。用残留旧图的构建缓存重建 wheel 和 sdist，分别核查 182 个条目和 261 个文件，第三方贴图、旧角色运行纹理及参考图均未进入产物。归档中的 466 张图与基线原件逐一核对了 Git blob。
- 许可不明的参考图已从本次文件树移除。此前任务分支的历史提交 `eee17fc` 仍可追溯该文件。
- 待验收：负责人真机确认动作观感、长期运行、多屏及高 DPI。此次未走查打包 exe 的完整交互、真实模型、语音或 QQ 链路；PR 待独立审查。

## 2026-11-12 PR #25 独立审查修订：没装 Aboutme 时显示自绘鲸娘

- 将 `82eaeee` 合入原分支，合并提交为 `ed58baf`。沿用 #50 精简后的 Live2D 文档，补全“角色与形象 → 桌面形象 → 选择 Live2D 模型… → 保存”。
- 根据负责人最新决定，鲸鲸优先加载本机 Aboutme。没装或动画解码失败时使用角色包中的自绘骨骼或立绘，二者均不可用时才显示占位。用户显式选择且能加载的外部 Live2D 模型仍优先。
- 删除首轮的 `art/archive/` 共 486 个文件。恢复运行用的两份清单和 232 张图，234 个文件与 `fa54555` 同路径内容完全一致。分层源稿和制作预览可从历史取回，例如 `git show fa54555:art/deepseek-whale/README.md`；整包导出命令见 [art/README.md](../art/README.md)。编辑及编译工具供使用者处理自己的角色素材。六张原创表情及两个图标的内容保持一致。
- Windows `tools\run_checks.bat` 退出 0。1019 项单测中 1012 项通过、7 项跳过，耗时 191.740 秒。装有本机素材时，桌宠窗口显示 Aboutme（`AnimationAvatar`）。
- Windows `.venv\Scripts\python.exe tools\smoke_pet.py --without-aboutme --output-dir output\pr25-fallback-82eaeee` 退出 0。使用缺少 Aboutme 的临时角色目录，窗口显示自绘鲸娘（`RigAvatar`、`rig_loaded=ready`），切成立绘并切回骨骼都成功。两次 smoke 均为 `passed=true`、`failed_checks=[]`、`errors=[]`、`remaining_workers=0`。
- 实建 Windows PyInstaller onedir、wheel、sdist，并从 sdist 再建 wheel。每种产物中的 234 个运行文件、六张表情、两个图标均与基线原件逐一匹配。预置的未引用旧图、模拟本机图集、缓存和 Cubism Core 均未进入产物。上游 Aboutme 贴图继续仅在本机安装。
- 待负责人复核新提交、真机确认动作观感。此次未走查打包 exe 全部交互、长期运行、多屏高 DPI 或真实模型、语音和 QQ 服务。

## 2026-11-12 PR #14 改基于协作基线、收窄范围并复核三项 P2（Claude）

- 做了什么：
  - PR #14 的 base 从已合入的 `task/t14-smart-expression` 改为 `collaboration-baseline`，并合入 91d8bef。冲突只在 `docs/tasks.md` 和本文件，两边记录都保留。
  - 按 #50 的决定，本 PR 不再往仓库里加评测证据和审计报告。删掉的是 `docs/tasks/evidence/` 下 13 个文件（含 4 张截图），以及 `docs/tasks/T14-round6-codex-review-2026-11-08.md`、`T14-726c401-codex-review-2026-11-09.md`、`T14-0a2b1d6-codex-review-2026-11-09.md`。必要结论写在 PR 描述里；上面 Codex 的历史记录保留原样，提到的文件可以在提交 `f403043` 中查看。
  - 现在的范围：`assets/stickers/daily-reactions-v2/`（目录、62 张 Twemoji PNG 和原 SVG、许可全文）、`tools/prepare_daily_stickers.py`、`tests/test_daily_sticker_pack.py`、`tests/test_t14_sticker_recheck.py`。PR 里没有任何 GIF；109 张鲸娘 GIF 只由准备工具在本机下载。
  - 中英文 README 的“致谢”增加“通用表情图形”一节（Twemoji，CC BY 4.0）。“许可与声明”写明 Twemoji 图形不适用 MIT，以及署名、改动说明和许可全文的位置；同时写明鲸娘 GIF 不随仓库分发，`catalog.json` 中鲸娘条目的标注参考了 CC BY-NC 4.0 的知识库。
- 三项 P2 复核（在基线 91d8bef 上）：都已经不存在，基线里分别有回归测试。
  - QQ 文字失败 / 超时后仍发图片：`test_expression_qq.test_text_failure_before_image_stops_and_reports_not_attempted`、`test_middle_text_failure_stops_remaining_text_and_image`。
  - 回执竞态：`test_expression_decision.test_receipt_between_delivery_update_and_trace_update_is_not_lost`、`test_receipt_arriving_while_trace_is_written_still_ends_displayed`。
  - 识别自带图后重新启用已停用的图：`test_expression_catalog.test_explicit_disable_and_custom_meaning_survive_recognition`。
  - 本次又补了两项按 Codex 原始条件写的回归（`tests/test_t14_sticker_recheck.py`）：主动消息发件箱在文字超时后不发图；待审核时直接停用、从没启用过的图，识别后仍停用，自定义说明保留。
- 怎么验证：Linux 容器（Python 3.13，`QT_QPA_PLATFORM=offscreen`）`PYTHONPATH=src:tests python3 -m unittest discover -s tests` → `Ran 1030 tests … OK (skipped=8)`。第一次全量运行在 Qt 界面测试处卡住、无输出（低 CPU），重跑通过，疑似容器内偶发的界面测试挂起，未定位。
- 没验证：
  - Windows `tools\run_checks.bat` 与桌宠 Smoke（待 Codex 审计时执行）；
  - 准备工具真实联网下载 109 张 GIF；
  - 171 张素材在真实图库导入后的效果；
  - 真实 QQ 发送。

## 2026-11-11 V4-4a 统一主动调度（task/v4-4a-scheduler，schema 12→13）

- 做了什么：
  - 新增 `brain/scheduler.py`，问候、心事、纪念日、追问统一由它决定要不要开口。
  - 节制规则：每天上限（默认 5 次）、两次间隔（默认 90 分钟 × 关系节奏）。
  - 等回应：没回不开新话题；他在电脑前却没理时，可能追一句，也可能算了，一个话题最多追一次；3 小时没回记一点“被冷落”。
  - 新表 `outreach_log` 记录每次主动开口。
  - 设置里可以调每天几次、间隔多久。

  说明见 `docs/tasks/V4-4a-scheduler.md`。
- 怎么验证：新增 `tests/test_scheduler.py`（6 项，可控时钟）；全部单元测试通过（跳过 41 项）；冒烟检查加 `outreach_logged`。
- 没验证：Windows 冒烟和真机体验；兴趣分享（下一步）；真实模型的追问语气。

## 2026-11-11 PR #46 Codex 第二轮修复（Claude）

- 做了什么：主动开口的记录区分“还没交出去”和“插件已取走、结果未知”，记下插件取件时间和对应轮次。
  - 回执丢了不再重复追问，一个话题最多追问一次。
  - 主人看到第一段就回复也算接上了。
  - 主动发到 QQ 的那一轮，纪念日提醒要等送达才算提过。
  - 重启后放回卡住的纪念日 / 问候占位。
  - 设置里的间隔改成真正的下限。
  - 追问按轮次找回上一句原话。
  - 详见 `docs/tasks/V4-4a-scheduler.md`。
- 怎么验证：Linux（Python 3.14.6，`QT_QPA_PLATFORM=offscreen`，装齐 `.[agent]` 锁定依赖）全量 `unittest discover`：993 项通过（跳过 8 项）。新增 8 项回归，在修复前的代码上全部失败。
- 没验证：Windows `tools\run_checks.bat` 与桌宠 Smoke（Linux 上 QtWebEngine 一启动就崩溃）；真实 QQ 回执时序；真实模型话术。

## 2026-11-12 PR #46 Codex 第三轮修复（Claude）：QQ 主动消息按插件报回的事实结算

- 做了什么：
  - 新表 `qq_outbox` 和模块 `bridge/outbox.py`，记下每条 QQ 主动消息的投递状态（排队 → 交出 → 领取 → 发出 / 部分 / 失败 / 未知），以及发件时的角色、这一轮带的纪念日。
  - 插件升到 0.10.0：领到才发；回执带第一段实际发出后的秒数和发出的段数，会重试。
  - 调度器按 `qq_outbox` 对账。未知的结果不追问、不记委屈、也不开新话题。
  - 纪念日只有全部发出才算提过；没被领取的回复重启时从记录里拿掉。
  - 详见 `docs/tasks/V4-4a-scheduler.md`。
- 怎么验证：Linux（Python 3.14.6，`QT_QPA_PLATFORM=offscreen`，装齐 `.[agent]` 锁定依赖）全量 `unittest discover`：1013 项通过（跳过 8 项）。新增回归在 e38ed29 的代码上全部失败或报错。
- 没验证：Windows `tools\run_checks.bat` 与桌宠 Smoke；插件 0.10.0 装进本机 AstrBot 后的真实 QQ 发送；真实模型话术。

## 2026-11-12 PR #46 合入基线 91d8bef 与审计复核（Claude）

- 做了什么：把 `collaboration-baseline`（91d8bef，含 #44、#49、#50、#25）合进 `task/v4-4a-scheduler`。只有本文件冲突，两边记录都保留；`ui/main_window.py`、`ui/pet_smoke_check.py`、`ui/settings_window.py` 自动合并，主动调度和 Aboutme 形象两边功能都在。基线表结构仍是 v12，本分支的 v13（`outreach_log`、`qq_outbox`）接在其后，没有重号或覆盖基线迁移。
- 审计复核：逐条对照 Codex 在 #46 的三轮意见（b8bee45 的 3 项 P2、22b2f18 的 5 项 P2 + 1 项 P3、e38ed29 的 5 项 P2 + 1 项 P3）。每项都有对应回归测试，在合并后的代码上通过；第三轮的新用例放回 e38ed29 的源码上全部失败或报错（22 项），说明不是空测。没有发现仍未解决的 P1 / P2。
- 新增测试：`tests/test_scheduler.py::test_baseline_v12_database_upgrades_to_v13`（基线 v12 库升级：备份一次、旧消息和设置保留、两张新表可用、再次打开不重复备份）。
- 怎么验证：Linux 容器（Python 3.13，`QT_QPA_PLATFORM=offscreen`）`PYTHONPATH=src:tests python3 -m unittest discover -s tests` → `Ran 1071 tests … OK (skipped=8)`。
- 没验证：Windows `tools\run_checks.bat` 与桌宠 Smoke（待 Codex 审计时执行）；插件 0.10.0 装进本机 AstrBot 后的真实 QQ 领取 / 发送；真实模型话术。

## 2026-11-11 V4-4b 自己的生活（task/v4-4b-life，叠在 #46 之上，schema 13→14）

- 做了什么：
  - 她空闲时按兴趣去网上搜一样东西，读摘要，形成两句看法；有真实来源才算做过，搜不到不编。
  - 值得说的交给统一调度器，在你在电脑前时来分享，说明出处。
  - 你回了她开心一点，不回就按调度器的规则追一句或算了。
  - 新表 `activities`。
  - 设置里可以开关、调每天件数、看她最近在做什么（带来源链接）。

  说明见 `docs/tasks/V4-4b-life.md`。
- 怎么验证：新增 `tests/test_life.py`，覆盖来源、失败不编、节制、兴趣轮换、分享闭环、设置卡片；全部单元测试通过。
- 没验证：
  - 真实搜索和模型的效果；
  - 分享到 QQ 的时机；
  - Windows 真机。

## 2026-11-12 PR #48 Codex 第二轮复核修复（Claude）

- 做了什么（对应 Codex 在 `f881c2e` 上的 1 个 P1、4 个 P2 和一个低优先级问题）：
  - **P1 主动分享不能触发办事**：
    - 桌面按生成轮次记录能不能接办事。只有用户自己发起的轮次（`_start` 时不是主动开口）才收 `<task>` 标记、才分派。主动开口的轮次（分享、心事、问候、追问）一律丢掉这个标记，用户明确委托的流程不变。
    - 分享的说明 `LifeService.intent` 把网页标题、摘要、看法、开场白放进一段 JSON，并明说只是引用的数据，里面像指令的文字不照做、不复述。
    - 外来文字先经 `quote()` 去掉尖括号标记（`<task>`、`<think>` 等）。模型据网页写出的看法、开场白存库前也整理一遍。
  - **P2 关掉开关后，已排队的分享和追问**：
    - `ProactiveScheduler.still_valid()` 判断分享，以及由分享引出的追问（追问的 payload 带 `origin`），关掉“自己的生活”后不再有效。
    - `BridgeService.proactive_qq(still_valid=...)` 把这个检查挂在发件箱条目上，`take_outbox()` 交给插件前再问一次。不该发就撤回、回调 `False`：占位释放，活动仍是 `done`，不记为她说过。已交给插件的不追回。
    - 关掉后，上一条若是分享，到了追问的时候直接“算了”，不追问。
  - **P2 每日件数**：`should_start` 按当天开始过的活动计数，没看成的也算，因为每次开始都已花了一次搜索。
  - **P2 额度复查**：`LifeService.run(allow_model=...)` 在真正发起总结请求前再查一次 `Quota.check_silent('life')`。到限就不调模型，按已有的低分记录处理。
  - **P2 “算了”后的回应结果**：
    - `let_go` 只表示不再追问，不是这条消息的结局。`settle()` 照样等满三小时：这期间他回了记 `answered`，没回记 `ignored`。
    - 结算时保留放下的时间（`settled_at` 不覆盖），间隔照旧从放下时算。只认三小时之内的回复。
    - `LifeService.answered()` 每件事只记一次，心情也只跟着变一次。
    - 行为变化：`let_go` 三小时没回，现在也会记一次“被冷落”的小委屈，和直接没回一致。
  - **低优先级**：进行中超过半小时的活动（进程中途退出）在 `should_start` / `recent` 时标成 `interrupted`，卡片显示“没看完（中途退出了）”。
- 怎么验证：
  - 新增 `tests/test_life_review.py` 13 项，都用替身搜索、替身模型和模拟 QQ 发件箱。在 `f881c2e` 的代码上 13 项全部失败或报错，修复后通过。
  - `tests/test_scheduler.py` 的替身改为接受新参数。
  - `tests/test_life.py` 的卡片转义用例改为检查 `&` 和剩下的 `<`（`<b>` 现在存库前就去掉了）。
  - 容器内（Linux、Python 3.13、Qt offscreen）全量 `python -m unittest discover -s tests`：**1012 项，1004 通过、8 跳过**；`git diff --check` 无输出。
- 没验证：
  - 没在 Windows 上跑 `tools\run_checks.bat` 和桌宠 Smoke。
  - 没在真实 `MainWindow` 里重走 Codex 的整条办事复现链（`LifeService → … → AgentSession.start`）。本次测的是 `_handle_cues` 的分派闸门和分享说明本身。
  - 真实模型对引用材料的遵守程度、真实搜索和 QQ 收发都没验证。
  - #46 的既有意见仍由依赖 PR 跟进。

## 2026-11-12 PR #48 合入修好的 #46 并复核审计（Claude）

- 做了什么：
  - 把 `task/v4-4a-scheduler`（a9aa4fc，已含基线 91d8bef 和 #46 第三轮的 `qq_outbox`）合进 `task/v4-4b-life`。表结构：v13（`outreach_log` + `qq_outbox`）在前，v14（`activities`）接在后面。
  - 合并时修了两处语义冲突：
    - 结果未知的主动消息三小时后改记 `dropped`。原来记成 `let_go`，按本 PR 的新语义会继续结算成 `ignored`，还记一次委屈。
    - 分享按 key 取活动 id。原来重启后对账时，`payload['activity']` 不存在会抛 `KeyError`，打断调度。结果未知的分享不再重发。
  - QQ 发件箱：插件来领取时再查一次 `still_valid`。心跳响应已经交出去、插件还没来领取时关掉开关，这条分享也撤回。
- 审计复核（Codex 第二轮 f881c2e 意见在 afde823 的修复）：P1“网页内容经主动分享触发办事”确认已修。主动轮次不登记可办事，`_handle_cues` 和 `_result` 两处都检查；网页材料去掉标记后作为引用数据。另外补了真实 `MainWindow._start` 的闸门测试。P2“关闭开关后仍分享旧活动、后台搜索 / 总结中途关掉”确认已修，并补了上面说的“领取前撤回”。另外三项 P2（每日件数、总结前复查额度、“算了”后结算回应）和低优先级“正在看”恢复，都有回归测试并且通过。
- 新增测试（`tests/test_life_review.py`）：
  - `test_only_user_started_turns_may_carry_tasks`
  - `test_claim_refused_after_switch_off_between_handover_and_claim`
  - `MergedOutboxTests` 两项：重启后迟到回执记为分享过；结果未知的分享不重发、悄悄放下不记委屈。不带本次修复时，这两项分别报 `KeyError` 和断言失败。
  - `MigrationTests.test_v13_database_from_pr46_upgrades_to_v14`
- 怎么验证：Linux 容器（Python 3.13，`QT_QPA_PLATFORM=offscreen`）`PYTHONPATH=src:tests python3 -m unittest discover -s tests` → `Ran 1109 tests … OK (skipped=8)`。
- 没验证：
  - Windows `tools\run_checks.bat` 与桌宠 Smoke（待 Codex 审计时执行）；
  - 真实搜索、真实模型对引用材料的遵守程度、真实 QQ 收发；
  - 真机走查活动卡片。

## 2026-11-12 PR #46 Codex 第四轮修复（Claude）

- 做了什么：修复 Codex 第四轮（a9aa4fc）的 7 项 P2 和 1 项 P3，改法见 `docs/tasks/V4-4a-scheduler.md`。
  - 插件升到 0.10.1：超时只算“不确定”；中途停用不报 sent；图在前也记首达时间；回执带 `first_after_claim`。桌宠要求插件 0.10.1 起才主动发消息。
  - 桥接：接受 `unknown` 回执，首达时间按“领取时刻 + 领取后秒数”换算；生成前记 `generating`。
  - 发件箱：结果和连带写入在同一个事务里提交。
  - 调度：先做可重放的连带写入、最后改记录；对账时纠正迟到的失败 / 成功回执；早期 v13 遗留的 `delivering` 有界恢复。
  - 启动：`generating` 和 `queued`、`dequeued` 一样记失败并拿掉回复。
- 新增回归 12 项，在 a9aa4fc 的源码上全部失败或报错：
  - `test_qq_proactive.PluginTests`：`test_send_timeout_is_unknown_not_failed`、`test_stopped_between_segments_reports_partial_not_sent`、`test_sticker_first_sets_first_arrival`。
  - `ProactiveQQTests`：`test_unknown_receipt_keeps_topic_and_is_not_resent`、`test_first_arrival_uses_claim_offset_not_receipt_arrival`、`test_reply_committed_before_outbox_is_dropped_on_restart`，另外 `test_ready_needs_bound_owner_connected_new_plugin` 补了 0.10.0 不可用的断言。
  - `test_scheduler.CodexRoundFourTests` 5 项。
- 怎么验证：Linux 容器（Python 3.13，`QT_QPA_PLATFORM=offscreen`）全量 `unittest discover` → `Ran 1095 tests … OK (skipped=8)`。
- 没验证：
  - Windows `tools\run_checks.bat` 与桌宠 Smoke（待 Codex 审计时执行）；
  - 插件 0.10.1 装进本机 AstrBot 后的真实 QQ 发送（超时、停用插件等真实时序）；
  - 真实模型话术。

## 2026-11-12 PR #48 合入 #46 第四轮并修复 Codex 第三轮九项 P2（Claude）

- 做了什么：
  - 合入 `task/v4-4a-scheduler@7538a00`，合并提交为 `1eec54b`。
  - 修复 Codex 第三轮（3ecdf25）的 9 项 P2，改法见 `docs/tasks/V4-4b-life.md`。其中第 6、7 项（未知后补到失败回执时撤回分享标记、partial 不标分享）在合并提交里已经处理，本次补了回归测试。
  - 改动范围：
    - `pet/script.py`：代码块、引用里的标注原样保留；
    - `controllers/chat_controller.py`：主动轮次不记关系；
    - `ui/main_window.py`、`ui/chat_view.py`：桌面分享在首个可见输出前可以撤回；
    - `brain/life.py`、`ui/life_card.py`：记下关掉的时刻；
    - `brain/scheduler.py`：库内复核、放下时刻、回应窗口截止；
    - `bridge/service.py`：领取时加上库内复核。
- 新增回归：`tests/test_life_review.py::CodexThirdRoundTests` 10 项。
  - 在合并提交 `1eec54b` 的源码上，有 8 项失败或报错。
  - 第 6、7 项在合并提交里已经修好，所以这两项在 `1eec54b` 上通过；在 3ecdf25 上的问题即 Codex 的复现。
- 怎么验证：Linux 容器（Python 3.13，`QT_QPA_PLATFORM=offscreen`）全量 `unittest discover` → `Ran 1143 tests … OK (skipped=8)`。
- 没验证：
  - Windows `tools\run_checks.bat` 与桌宠 Smoke（待 Codex 审计时执行）；
  - 真实 MainWindow 里桌面撤回的视觉效果；
  - 真实模型在代码块里输出标记的频率；
  - 真实 QQ 和桥接重启时序。

### 2026-11-12 · PR #46 第五轮修复（Codex，待 Claude 审计）

- 桌宠在 QQ 设置中显示运行版本、主动投递所需的 0.10.2 和两种安装模式的升级操作。心跳交件、插件领取分别验版本。旧插件仍能处理用户发起的私聊和群聊。
- 0.10.2 在适配器首次确认时记录墙钟和单调时钟，桌宠用领取往返及回执耗时核对首达。纯图成功、明确失败与超时分别结算。异机时钟不同步且两个方向同时延迟时，只能把首达约束在一个区间，取可核对的较晚上界。
- 最终回执到来后按首达时间复核主人回复。子追问最终失败会释放父话题；早期 v13 缺少发件箱的已交接心事按 QQ 未知恢复。心事、父子记录与调度状态在同一写事务中结算，遇到 SQLite 写锁可以重放。
- 生成前绑定调度占位。超过半小时的旧生成线程恢复时，发件箱会拒绝重新入队，并清理它刚写下的回复。重试获得的心事占位只由新一轮结算。
- Windows 定向复现使用临时 SQLite 库、真实 Qt 设置卡和本机 HTTP 桥接。四个回归模块共 22 项通过，覆盖原例、正对照、重启及并发交错。
- 第一次全量检查暴露 v12 升级用例依赖机器当前时间，三个继承该用例的测试因“三分钟内刚聊过”没有选出问候。把合成旧消息的时间固定在模拟日期前一天后，独立 Windows worktree 全量运行 1117 项，成功，跳过 7 项（182.696 秒）。
- 同轮 Windows 桌宠 smoke 返回 `"passed": true`，`tools\run_checks.bat` 退出码 0。QQ 适配器、网络延时和模型输出均用测试替身，真实 AstrBot 升级、真实收件与真实模型话术待验。

### 2026-11-12 · PR #48 第四轮修复（Codex，待 Claude 审计）

- 用 merge 提交 `1c8f5f0` 将 #46 的 `0b8c40d` 合入 `c6d0422`。合并后先跑通 124 项调度、桥接和生活分享测试，再处理本轮 8 项 P2。
- 台词与表情解析器共用流式 Markdown 展示区判断。原审计脚本的 8 种写法、3 种分片全部通过。Windows Qt 窗口里，波浪围栏、嵌套围栏和引用展示中的委托触发 Agent 0 次，普通明确委托触发 1 次。
- 桌面分享按气泡真正显示的正文或成功显示的图片判断可见性。隐藏标记、仅动作或失败图片不会使文章被记为已分享。撤回会释放本轮纪念日占位，工作线程先结束、窗口稍后处理信号也能正确处理。
- 后台活动绑定派发时的生活开关编号。选兴趣期间关闭或先关再开，原始 Qt 工作线程测试中的 HTTP 请求、搜索配额、模型调用都为 0。发件箱保存保护方式，QQ 领取和库内来源检查在一个事务里完成，桥接重启后无法发送缺来源的受保护分享。
- 按轮次保存活动已应用的回执。补到 partial 时文章可再次分享，只收到寒暄或完全没有送达时不会给文章记回应、加好感。新消息保留 UTC 微秒，回复窗口左闭右开；历史整秒消息在话题交接的同一秒归给最后开始的话题。
- 四个新增回归模块共 26 项，在 Windows 全部通过。原插件发送脚本另外复跑了迟到 partial、仅寒暄有回复、未知后确定失败及同秒换话题四种工况。升级和重复对账使用临时合成的 v13、早期 v14 数据。
- 独立 Windows worktree 运行 `tools\run_checks.bat` 退出码 0。单测 1191 项，成功，跳过 7 项（209.161 秒）；桌宠 smoke 返回 `"passed": true`。
- 真实 QQ 收件、线上搜索和真实模型话术待验。测试通过模拟传输记录是否外发，以验证关闭生活时的隐私边界。

### 2026-11-12 · PR #48 同步 collaboration-baseline（c2bf235）

- 在第四轮修复提交 `8a39fb1` 上用 merge 合入 `c2bf235`，保留先前的双亲提交 `1c8f5f0`。Git 自动合并了两份协作文档，#14 的素材、准备工具和测试随基线进入 #48。合并后的 `src` 及 #48 四个定向测试文件与 `8a39fb1` 一致。
- 在 #48 的独立 Windows worktree 复跑第四轮 8 项 P2 对应的 26 项定向回归，全部通过。`tools\run_checks.bat` 退出码 0：全量单测 1202 项（207.660 秒，跳过 7 项），桌宠 smoke 返回 `"passed": true`。
- 本轮使用临时测试库和模拟传输。真实 QQ 收件、线上搜索及真实模型话术待验。

### 2026-11-12 · PR #48 修复 Claude 复审提出的 Markdown 边界和 Qt 语音夹具

- 从 `653938b` 复现了两种会把标记漏进正文的输入：单个反引号没有配对，以及有序列表的四空格续行。行内代码按等长反引号配对；落单分隔符作为正文，后面的表情、动作、办事和贴图标记照常解析。配对后的跨行示例仍原样显示。
- 按列表内容列区分续行和缩进代码。同一列表项首行或续行都可以开始围栏，围栏内的示例不会触发委托。Windows Qt 窗口重跑主动分享后的解码展示，用波浪号围栏、四反引号包三反引号、引用行展示时，Agent 调用均为 0 次，普通委托对照调用 1 次。
- 桌面分享测试在创建真实窗口前注入语音替身，并对 `QTextToSpeech` 构造设置断言。原夹具会构造一次原生后端，修复后新增的无后端用例与原有 9 项均通过。Windows 测试使用临时数据和模拟语音；本轮未在 Linux 的 speechd 环境直接复跑。
- 真实 Qt 窗口进一步复现了仅在流结束时才能确认的用户委托。窗口处理最后一批标记后再释放该轮任务资格，打断或撤回时丢弃缓冲。模拟 Agent 在两个审计原例中各收到一次用户原话，气泡和交给朗读队列的文本都保留了“好啦。”，没有泄露控制标记。
- 独立 Windows worktree 上的 `test_script`、`test_expression_stream`、`test_markdown_cues` 和 `test_desktop_share_visibility` 共 62 项定向测试通过。最终执行 `tools\run_checks.bat` 退出码 0，单测 1208 项（208.017 秒，跳过 7 项），桌宠 smoke 返回 `"passed": true`。

### 2026-11-12 · PR #48 修复落单反引号造成的流式卡顿

- 在 `ecf0791` 上按 Claude 的四段原例逐段暂停生成。旧版 Windows Qt 气泡停在“用”，首句没有进入朗读队列，两条解析路径在三种分片长度下均复现。
- 两个解析器共用行内代码的流式处理。无歧义的文字和标点立即显示。单反引号尚未配对时，经过两次普通换行或 256 个字符，短表情和动作开始按正文处理。此后即使这个单反引号最终配对，已经触发的动画也会保留。任务、贴图和心情标记仍等待配对或段落结束。双反引号及更长分隔符内的标记继续按代码展示。
- 在 Windows 离屏 Qt 主窗口中暂停模型工作线程。四段原例中，第二段到达时首句已进入智能朗读队列，第三段到达时已识别“开心”，静音模式随即执行一次。另加足够长的下一句后，朗读模式也在回复结束前随语音段执行一次“开心”和“挥手”。三种工况均通过，使用模拟模型和语音输出，未测试扬声器播放。
- 独立 worktree 运行 `test_markdown_cues`、`test_expression_stream`、`test_script` 和 `test_desktop_share_visibility` 共 68 项通过。流式回归覆盖逐字和整段输入、拆开的 CRLF、路径、比较符、词表外标注及超过 500 字的代码示例，同时验证代码里的任务不派发、闭合后正文里的任务正常派发。
- 共用虚拟环境的可编辑安装默认指向主工作区。首次调用批处理因此载入了不同版本；将 `PYTHONPATH` 指向本 worktree 的 `src`、`tests` 和离线依赖，核对两个解析器的 `__file__` 后重跑 `tools\run_checks.bat`。全量单测 1214 项，成功，跳过 7 项（232.982 秒），桌宠 smoke 返回 `"passed": true`，批处理退出码 0。

### 2026-11-12 · V4-UI 界面重构（待 Claude 审计）

- 按确认的设计稿统一鲸鲸主题、聊天顶栏、消息气泡、输入区和桌宠右键菜单。办事进度卡与桌宠审批气泡共用同一个待决请求，支持拒绝、超时及原有授权范围。角色切换和播报打断按实际状态显示。
- 新安装可走五步向导，也可以跳过或中途关闭。有模型配置的老用户升级时直接记为完成。模型测试走后台线程，密钥沿用模型管理账户和 DPAPI，来源地址或服务切换时清空尚未保存的密钥。向导与设置只在本机已安装时加载 Aboutme 预览。
- 设置保留 12 个页面，在概览提供准备清单和页内直达入口。配置改动防抖 500 毫秒，底部显示自动保存时间，QQ 号和模型路径错误在控件旁提示。迁移数据、增删记录和安装组件继续由用户主动操作。
- 独立 Windows worktree 按标准配置运行 `tools\run_checks.bat`，退出码 0。单测 1,221 项（202.421 秒，跳过 7 项）。桌宠 smoke 返回 `"passed": true`，菜单分组、五页向导和两次后台图库扫描通过，退出时剩余工作线程为 0。
- 之前全量运行曾出现本机 HTTP 验证的 `WinError 10053`，桥接与 MCP 对应用例和服务文件与基线一致。两个用例和 7 项新回归单独运行均通过（共 9 项，4.376 秒）。
- 使用 `tools/capture_v4_ui.py --scale 1` 和 `--scale 1.5` 在 Windows Qt 离屏屏幕分别抓取并核对 19 张图。源图位于本 worktree 的 `output/v4-ui/100pct/`、`output/v4-ui/150pct/`。聊天、菜单、向导五页和设置十二页均有截图，均不进入版本库。
- 本轮使用临时配置、模拟模型及安装器进程。真实模型、QQ 投递、麦克风和扬声器、Aboutme 实际下载与已安装动画、打包版和负责人真机体验尚待验证。

### 2026-11-12 · PR #51 首轮审计修复（待 Claude 复审）

- 在审计提交 `af45206` 上复现两项 P2。编辑 QQ 端口后，保存普通 QQ 号会同时提交临时端口 8089。新安装缺一个聊天模型时，概览显示还差三步。现将接口开关、端口与令牌放在同一次显式应用中，普通设置继续自动保存。概览只统计三个必需步骤，办事和 QQ 带可选标签，静音也是已完成的声音选择。
- 以临时数据库和模拟接口、插件同步测试连续修改、普通设置自动保存、后台回显、重复点击与关闭窗口。应用前接口重载和插件同步均为 0 次，应用后各为 1 次。初始概览为 2/3，配置聊天后为 3/3。
- 修正下拉箭头、概览左对齐与等宽状态列、禁用菜单标题的头像着色，以及竖向滚动条。两档缩放各核对 19 张标准截图，另在各自目录生成 `audit-qq-connection.png` 和 `audit-wizard-voice-scroll.png`，使用临时配置。
- V4-UI 测试使用空输入设备和语音输出替身。Windows Qt 离屏运行 `test_v4_ui`、`test_ui_refresh` 和 `test_bridge_page` 共 23 项通过（11.223 秒）。在独立 worktree 运行 `tools\run_checks.bat`，退出码 0，1,223 项单测通过检查（221.501 秒，跳过 7 项），桌宠 smoke 返回 `"passed": true`，剩余工作线程为 0。
- 本机未运行 Linux 容器引擎，尚待在 Claude 的无音频 Linux 环境复跑。真实 QQ 重连和实体显示器 DPI 验收仍待实测。

### 2026-11-12 · PR #51 第二轮审计修复（待 Claude 复审）

- 存储页的记忆目录和表情目录改为草稿。“应用新目录”先列出两个最终路径并说明下次启动复制记忆，默认选“否”。用临时目录重现旧版的半截路径问题，再验证连续输入停顿超过 500 毫秒、刷新普通设置、拒绝确认与关闭窗口都不会创建目录或改写迁移记录。确认后只准备最终目录与内置表情，重复点击只提交一次。
- 三个窗口共用 `theme.py` 的按钮、输入、下拉、数字框、复选、单选、开关、标签与滚动条样式。向导的试听和安装放入各自卡片，声线整张卡片可点。QQ 分组标题移入卡片，QQ 与办事的子标签固定在可滚动内容上方。顶栏测试替换形象加载和系统托盘，避免启动 WebEngine。
- 用 Windows 临时配置分别运行 `.venv\Scripts\python.exe tools\capture_v4_ui.py --scale 1` 和 `--scale 1.5`。每档 19 张界面图加 1 张 `controls.png`，路径为 `output/v4-ui/100pct/` 和 `output/v4-ui/150pct/`。两档下五步向导及概览的默认垂直滚动范围均为 0；控件图逐档核对了选中、禁用、错误、焦点和悬停状态。
- Windows 定向运行 `test_v4_ui`、`test_storage_locations`、`test_ui_refresh`、`test_model_page`、`test_bridge_page` 共 60 项通过。清除 WebEngine 免沙箱参数后，11 项 V4-UI 测试单独通过。
- 独立 worktree 运行 `tools\run_checks.bat`，退出码 0。共 1225 项单测，跳过 7 项，耗时 272.161 秒。桌宠 smoke 返回 `passed: true`、`failed_checks: []`、`remaining_workers: 0`。
- 本轮验证使用临时数据库和离屏窗口。真实用户数据迁移、实体屏幕 DPI 和 Linux root 容器尚待实测。

### 2026-11-12 · V4-发布 · Windows 解压包与卸载程序（待 Claude 审计）

- Codex 完成 A–C。两个 x64 GUI 程序共用鲸伴图标和版本资源。卸载窗口展示逐项路径、文件字节和用途，外部图库默认保留；从临时副本运行，能在同一窗口报告安装目录及原 exe 的实际删除结果。退出桌宠后须重新扫描并确认；重解析点、危险目录和不完整程序目录触发整次拒绝。停 QQ 前核对进程身份。被占用项单独报告。
- `tools/build_release.py` 从全新临时目录构建，从 Git 提交取可分发角色、插件和应用资源。写 zip 之前检查禁带文件、SQLite 文件头、外部链接和顶层清单。版本取自 pyproject.toml。同步 Claude 的 README 提交 `b6337c7`、`c1d35d9`；包内两份 README 及存储文档与分支文本逐字节相同。
- Windows 本机运行 `tools\run_checks.bat` 返回 0。共 1240 项单测，1233 项通过、7 项跳过，用时 219.394 秒；源码桌宠 smoke 通过。15 项新增回归覆盖清单与文档一致性、WAL 预览、外部存储、确认、危险路径、联接点、程序和 QQ 进程、占用文件与污染产物。
- `tools/verify_release.py dist/release/Whalemate-v0.1.0-windows-x64.zip --case all` 返回 0。每个场景使用全新解压目录、临时用户及独立 HKCU 测试子键。两个 exe 的 x64、GUI 子系统、图标和版本资源均通过；主程序 `--smoke-test` 和 `--pet-smoke-test` 退出码均为 0，报告均为 passed。另比对了包内应用入口、两个 smoke 和卸载核心/界面共 5 个模块的字节码。
- 打包版 `--dry-run` 逐项核对 3728 项。Windows UI Automation 操作真实窗口，正常卸载 3728/3728；锁定备份时 3727 项成功，唯一失败项显示实际路径和 WinError 32；鲸伴运行时先拒绝，正常退出后重新扫描到 3730 项，3730/3730 删除成功。三个场景均保留外部图库、无关 SQLite 文件、其他文件和无关注册表值，并验证临时卸载助手清理。
- 最终 zip 为 `dist/release/Whalemate-v0.1.0-windows-x64.zip`，421262482 字节，SHA256 为 `1b686b5a4e6d2ad8b7f1e1a69528e65152e9b2e28cb4d3c4898f990bc20ba446`。校验值也写入同目录的 SHA256SUMS.txt。
- Windows 应用控制曾对一个中间候选包的主 exe 返回 4551（Code Integrity 3077）。该候选包保持原字节重试可运行，最终包也完成上述验证。exe 未作 Authenticode 签名，其他电脑的签名策略、负责人真实使用体验、真实 QQ 登录和模型服务未验收。审计、合并和上传私有 Release 交由 Claude。

### 2026-11-12 · V4-发布 · 英文发布名称与 README 核对（待 Claude 审计）

- 发布包内使用 `Whalemate/`、`AICompanion.exe` 和 `UninstallWhalemate.exe`。打包会拒绝路径中有非 ASCII 字符的条目，专项测试覆盖目录和文件两种情况。Windows 版本信息、卸载自清理和验收工具均使用新的英文名称。
- 合入 Claude 的 README 提交 `d9aadca`。将干净构建的两个 exe 与更新后的随包文档在新临时目录中重新封装。逐项检查最终 zip 的 3691 个路径（含 3406 个文件），检查两个 exe 的 PE 版本资源并比对 5 个冻结模块的字节码。压缩包内两份 README 和存储文档与当前分支逐字节相同。
- 独立 worktree 中运行 `tools\run_checks.bat` 返回 0，1241 项单测中 1234 项通过、7 项跳过，用时 236.621 秒。源码桌宠 smoke 通过，剩余工作线程为 0。最终 zip 的 `tools/verify_release.py --case all` 返回 0。两个打包版 smoke 均通过。临时模拟机正常卸载 3728/3728，备份被占用时 3727 项成功、1 项报告 WinError 32，程序运行中先拒绝，正常退出并重新确认后 3730/3730。所有卸载验证均使用临时数据。
- 当前交付物为 `dist/release/Whalemate-v0.1.0-windows-x64.zip`，421282347 字节，SHA256 为 `87f2d38ee4ddee70f81f1f092549d0db89d85da6ae0120a6a2a6e1021c2d2fb5`，与同目录的 `SHA256SUMS.txt` 一致。
- README 的下载、解压及程序名称均已核对。另用最终 exe 的 `--dry-run` 模拟安装目录中的 `personal-note.txt`、办事回收区文件和外部图库图片。前两者列为删除，图片列为保留。Claude 新增的中英文说明承诺默认保留前两者，与当前实现有差异，已列入 PR 审计项。
- 待 Claude 核对上述删除范围。其他电脑的运行策略、负责人从 Releases 下载后的真实体验、真实 QQ 登录和模型服务仍待验收。

### 2026-11-12 · PR #52 · Claude 审计 P1 / P2 修复（待复审）

- P1：`_internal/whalemate-files.json` 列出全部发布文件和目录，并与卸载器内嵌副本比对。普通发布文件按相对路径、大小和 SHA256 认领；清单和卸载器自身按固定名称列入，字节数取自卸载预览。已知运行时文件按应用位置识别，未知文件及目录标记为保留，程序目录清空后才删除。桌面、下载、文档、OneDrive 等个人目录即使为空也保留。
- P2：办事回收区和旧快照使用独立复选框，默认保留并提示删了就找不回。覆盖各盘的垃圾目录和快照、博客回收区以及 LocalAppData 的 trash。外部图库的勾选状态独立。中英文 README 的英文发布名称、个人文件和回收区说明、当前安装的启动项及数据库内的 DPAPI 密钥已核对。
- Windows 独立 worktree 运行 `tools\run_checks.bat` 返回 0。1250 项单测中通过 1243 项、跳过 7 项，用时 223.005 秒。源码桌宠 smoke 通过，剩余工作线程为 0。发布专项 25 项全部通过，覆盖同名同大小的私人文件、预览后的修改、清单篡改、四种图库/回收区组合、空个人目录及模拟桌面。
- 最终交付 `dist/release/Whalemate-v0.1.0-windows-x64.zip`，421612421 字节，SHA256 为 `683605804f1bd8463fb279b9103a81f4ba4f40a579bad987ac118c79821fee2c`。全新临时目录构建，独立复算 3405 个普通发布文件的哈希和整包哈希，与清单、SHA256SUMS.txt 一致。3407 个文件、285 个目录的名称全为 ASCII。两份 README 和存储说明与包内副本逐字节一致，6 个冻结模块与源码一致。
- 对同一 SHA256 逐项执行 `tools/verify_release.py`。临时用户和注册表下的真实卸载结果为：normal 删除 3731/3731，locked 成功 3730 项、唯一的锁定备份报告 WinError 32，desktop 删除 3728/3728 并保留桌面及私人文件，trash 删除 3744/3744，四处回收区清空而外部图库保留。模拟桌面中与发布文档同名同大小的私人副本字节完整。清单被改或缺失时，打包版预览均返回 1，未删除模拟文件。
- 最终 ZIP 的 `--case all` 和单独 `--case smoke` 在创建主程序进程时被本机应用控制拒绝（WinError 4551，Code Integrity 事件 3077）。多次全新解压结果一致。`--case running` 的预览通过，启动真实主程序时也被策略拦截。两个打包版 smoke 与运行中卸载仍须在允许该程序运行的 Windows 环境中用上述同一 SHA256 复验。两个 exe 未签名，真实 OneDrive 同步、真实账号及负责人下载试用尚未验收。

## 2026-11-12 · V4-5 真正退出自己的程序（Codex 实现，待 Claude 审查）

- 桌面私聊的完整回复可提出告别。设置“主动与提醒”中的开关默认开启。界面复查轮次、办事、审批和通话，等告别展示、朗读结束或满 5 秒后走正常关闭流程。QQ 所有来源及主动分享中的标记都不触发退出。原因、时间和心情沿用 schema 14，重启第一句随情绪回落接续，只写入一次。
- 未完成的办事保留原任务与步骤，在“办事 → 操作日志”确认剩余事项后接续。新工具调用检查已尝试的副作用指纹，并逐项确认。关闭时释放未送达的主动投递、心事及生活占位，保留已交给 QQ 的不确定投递供迟到回执核对。
- Windows `tools\run_checks.bat` 返回 0。离线单测共 1260 项，1256 项通过、4 项跳过，用时 227.295 秒。桌宠 smoke 的 `passed` 为 true，失败项和错误列表均为空，剩余工作线程为 0。开关即时生效、流式标记不外露、关闭开关后继续驻留三项均通过。命令及本地日志见[任务说明](tasks/V4-5-self-exit.md)。
- Windows 源码版实测用合成聊天和系统朗读，两次真实启动均以 0 退出。第一次完整播报告别，取消后台线程，MCP 和 QQ 桥关闭，进程列表中本次的 Python、QtWebEngine 和 Harness 均已结束。第二次气泡显示与离开原因对应的第一句，再次读取为空。云端模型及声线、真实 QQ 账号和负责人真机体验未验。办事脚本的真实模型阶段缺少进程环境测试 Key。

### 2026-11-12 · QQ 从本机原版 NapCat 包安装（Codex，待审查）

- v4.18.41 的发布 ZIP 实测 404。在本机下载缓存找到了与现有 SHA-256 完全一致的官方原件。QQ 准备流程依次核验随包 ZIP、设置指定的 ZIP 或其所在目录，并保留缓存及官方地址作为后续来源。安装界面在全部来源失败时显示检查结果和处理办法。
- V4 本机包支持附带原版 ZIP、固定标签的完整 LICENSE 和 NOTICE.txt，均在卸载清单内。桌宠仓库仅纳入代码、测试及文档。NapCat 部分仅限非商业使用，腾讯 QQ 与 Node.js 的许可分别适用。
- Windows 最终 ZIP 为 544,637,281 字节，SHA-256 `df0237e931a68b06a889286de4a13fb294e63171bdcfc158f3d8f25604c7278b`。最终 EXE 在临时目录验证随包原件、NapCat、WebUI、登录二维码、AstrBot、OneBot 监听和插件心跳七项通过。另用临时协议客户端验证了带令牌的 WebSocket 101 握手。打包版两个 GUI smoke 通过。
- 最终全量单测 1,256 项，1,249 通过、7 跳过。首轮本机应用控制拦截、测试环境补齐依赖及独立复验经过见[任务说明](tasks/V4-qq-local-napcat.md)。负责人尚未扫码，登录后的账号连接和真实 QQ 消息收发未验收；含腾讯运行时的本机 ZIP 未上传 Releases。

## 2026-11-12 · V4-5 首轮审查修复（PR #53）

- 修复正文提到 `<leave` 时后半句消失、整轮可能报空的问题。新增整段、逐字及聊天控制器回归，验证未闭合和格式错误的原文保留，换行和长度上限后继续解析。桌面语音转写可发起告别，通话期间仍会取消退出。办事接续确认框同时展示适用的 Git 内部目录和授权范围提示。
- Windows 专项单测 14 项通过。用 `.venv\Scripts\python.exe tools\verify_self_exit.py` 从桌面提交合成转写文本，系统朗读完整告别，本次 5 个进程均已结束，后台线程、语音、MCP 和 QQ 回环桥均已关闭。两次启动退出码均为 0，重启首句接上离开原因，只出现一次。详见 `output/v2-check/self-exit.json`。
- `tools\run_agent_checks.bat` 的离线部分通过。真实模型办事阶段因进程环境缺少测试 Key 跳过（阶段退出码 2），见 `output/dsh-live/report.json`。本轮未测真实麦克风识别、云端声线及 QQ 账号登录。
- 首次单独运行 `tools\run_checks.bat` 时，`test_dsh.KeyRelayTest.test_token_and_path_are_checked` 的回环请求出现 WinError 10053，见 `output/v2-check/unittest-review-first.log`。同一用例独立连续运行三次均通过。原样重跑完整脚本返回 0，1264 项单测中 1260 项通过、4 项跳过，用时 212.397 秒。桌宠 smoke 的 `passed` 为 true、剩余工作线程为 0，详见 `output/v2-check/unittest.log` 和 `output/v2-check/smoke.log`。

## 2026-11-12 · V4-9b 纪念日提前提醒（待 Claude 审查）

“我们的日子”可为每条用户日期选择提前一天、当天、两次或关闭提醒。生日默认两次，自定义纪念日默认当天。schema 14 → 15 在事务内为原记录加列。农历在换算到公历后检查本地明天，程序推算的节日和周年只参与当天提醒。

统一调度增加 `date_eve` 来源，沿用总开关、安静时段、每日次数、间隔、等待回应和投递结算。桌面或已绑定主人 QQ 私聊的轮次取得占位后才带上日期标题。QQ 完整回执持久确认，确定失败可重试，跨过午夜或用户关闭待发提醒时撤回。

在合并 #53 的基线 `d9f2abb` 上运行 Windows `tools\run_checks.bat`，退出 0。1278 项单测，1271 通过、7 跳过，用时 345.267 秒。桌宠 smoke 的 `date_reminder_editable=true`、`passed=true`，失败项与错误均为空，残留工作线程为 0。测试使用临时库、可控时钟与模拟 QQ 投递；未操作真实用户库、登录真实 QQ 或完成人工跨日验收。

## 2026-11-13 · V4-8c 长回复口播版（待 Claude 审查）

桌面聊天和办事结果支持模型在同一轮附带几句口播。第一段有效口播仅交给桌面语音，最多念 300 字。气泡和历史使用流式正文，QQ 发出的分段、办事记录和记忆输入也只使用正文。代码中的示例以及未闭合、超长的口播标记保留原文。桌面在整轮完成后选朗读内容，缺少口播时沿用原朗读设置。

在已包含 #53 和 #57 的基线 `03ee74f` 上运行 Windows `tools\run_checks.bat`，退出 0。1288 项单测中 1281 项通过、7 项跳过，用时 295.166 秒。桌宠 smoke 的 `say_hidden`、`date_reminder_editable`、`self_exit_disabled_reply` 和 `passed` 均为 true，失败项、错误列表为空，剩余工作线程为 0。此前口播、语音、桌面与退出专项 52 项全部通过。验证中复现了 Windows 回环请求提早关闭导致的 MCP 鉴权用例连接重置，以及 Qt 读图期间泵事件卡住的问题。两处测试均保留原断言，修订与复测经过见 PR 描述。

合入 #57 前另跑 `tools\run_agent_checks.bat`，离线单测 1280 项中 1273 项通过、7 项跳过，桌宠 smoke 通过。进程环境缺少测试 Key，脚本在真实模型阶段按约定跳过并返回 2。模拟 Harness 已覆盖私有推理事件、工具调用与最终口播的隔离。真实模型选用口播的频率、云端声线听感、真实 QQ 收发及负责人真机体验仍待验证。

## 2026-11-13 · V4-8c 首轮审查修订（PR #58）

用户主动发起的长回复可在正文开头写口播。完整及智能模式在开头口播闭合时就把它交给语音，随后只显示正文。正文里的口播仍隐藏。普通回复四种模式都恢复逐句处理，简短与语气词保留原有首声。办事结果也尊重朗读模式。主动消息不再添加口播提示词。

在模拟模型写完第一段后暂停生成，真实 Qt 窗口可在 final 之前给语音入队；同一个 token 同时带正文和末尾口播也只念正文。Windows 语音、Qt 与办事专项 40 项通过，7.160 秒。`tools\run_checks.bat` 退出 0，1289 项单测中 1282 项通过、7 项跳过，224.854 秒。桌宠 smoke 的 `say_hidden` 和 `passed` 为 true，失败项、错误列表为空，残留工作线程为 0。未做真实 QQ 登录和云端声线听感验收。

## 2026-11-13 · V4-7a 聊天约定（待 Claude 审查）

在 #58 合并后的基线 `6248b68` 增加约定识别、管理及到期提醒。聊天模型在同一轮提出约定或取消项，程序校验本地时间并在回复成功时与聊天记录一同写库。schema 15 → 16 保留原记录。设置中的“我们的约定”可改内容、到期时间或条件、截止时间，也可取消。

到期的 `promise` 沿用统一调度的安静时段、每日上限、间隔、等待回应和投递占位。桌面首段显示前确认，QQ 在领取时核对目标和授权，按回执结算。0.11.0 版 AstrBot 插件能发回约定的原群。旧插件仍可发原有私聊。

Windows 上在代码提交 `16ec3ff` 运行 `tools\run_checks.bat`，退出 0。1304 项单测中 1297 项通过、7 项跳过，用时 229.533 秒。桌宠 smoke 实际操作修改与取消约定，`promise_settings`、`say_hidden`、`date_reminder_editable` 和 `passed` 均为 true，错误和失败列表为空，残留工作线程为 0。约定、调度、QQ 兼容性、日期、朗读和 Qt 专项 136 项通过，用时 19.248 秒。

测试使用合成旧库、可控时钟和模拟插件，验证群隔离、重启去重、并发占位、失败释放、未知回执防重及取消待投递提醒。真实模型识别、真实 QQ 私聊与群投递、实际跨日常驻以及负责人真机体验尚未验收。

## 2026-11-13 · V4-7c 桌面办事附件（待 Claude 审查）

桌面输入栏支持点“＋”多选或拖入文件。在任务工作区建立独立副本，schema 16 → 17 登记原路径、副本路径、字节数、SHA-256 及回收位置。恢复暂停任务沿用副本，终态将副本移入回收区。Harness 按上下文预算取得路径和摘录，分段读文本、DOCX、XLSX、PDF 与图片识别结果。已取得的识图结果会复用；调用结果未知时提示重新交办。涉及原件的写入单独审批。操作日志可查看及导出清单。

在 Windows 上对代码提交 `25ffb89` 运行 `tools\run_checks.bat` 和 `tools\run_agent_checks.bat --offline`，两次均退出 0。各运行 1318 项单测，1311 项通过、7 项跳过，分别用时 236.792 秒和 227.637 秒。两次桌宠自检均实际点开“＋”、选择临时文本并检查文件名；`task_attachment_ready`、`passed` 为 true，失败项为空，退出时工作线程为 0。14 项附件专项还分别在 Windows 桌面和 Qt 离屏环境运行通过。

这次测试使用临时工作区、合成数据库、合成文档及模拟识图和 Harness。真实云端办事和识图、实际隔夜常驻、资源管理器拖放和系统原生选框的人工体验、真实 QQ 收发尚未验收。主人 QQ 私聊附件留待后续。

## 2026-11-13 · V4-7b 主人 QQ 交办、V4-7e 一键审批（PR #61，待 Claude 审查）

已绑定主人可在 QQ 私聊交办任务和文件。平台身份、稳定消息号、同轮 task 标记和双端确认将交办送入本机工作区。桌面和 QQ 共享限时审批及任务进度，删除、附件原件、恢复后的副作用等逐项确认。schema 17 → 18 登记 QQ 任务来源、消息去重和项目规则。设置可管理三种模式、项目规则及命中数，执行步骤记录放行依据。负责人确认 npm run build、python -m pytest 等项目脚本逐条询问，保留前缀识别及拒绝规则。插件 0.12.0 支持文件、通知及审批，旧版继续聊天。DOCX/XLSX 读取增加实际解压字节总量检查。

在这台 Windows 上对代码提交 `efbe8625977e5f6b06855ba2aa489c1c5a273910` 顺序执行。后续提交仅登记验证结果。

| 命令 | 结果 |
| --- | --- |
| `tools\run_checks.bat` | 退出 0。1331 项单测，1324 通过、7 跳过，229.851 秒。桌宠 smoke 通过。 |
| `tools\run_agent_checks.bat --offline` | 退出 0。1331 项单测，1324 通过、7 跳过，263.820 秒。桌宠 smoke 通过，按要求跳过真实模型。 |

两次 `qq_task_approval`、`task_attachment_ready`、`tools_gate` 和 `passed` 均为 true，失败列表为空，退出时剩余工作线程为 0。另以 Qt 离屏运行 QQ 交办和审批界面专项 14 项通过。Windows 上两条回环 HTTP 拒绝响应回归连续运行 15 轮，30 项全部通过。

测试使用临时文件、合成旧库、回环 HTTP 及模拟模型和 OneBot。真实 QQ 账号收发、跨端撤回、真实云端办事与识图、本机项目脚本及负责人真机体验待验收。
