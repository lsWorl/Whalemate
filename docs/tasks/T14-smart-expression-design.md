# T14-0：智能表情表达协议与接入设计

日期：2026-11-08。负责：Codex；待 Claude / 项目负责人复审。状态：文档冻结 v1，尚未实现。

本文的“冻结”表示后续实现和测试采用同一份明确契约，仍须负责人审核；不代表运行时代码已具备这些能力。T14-1～T14-6 均未开始。本轮没有下载图片、修改运行代码 / schema / 自定义人格 / 记忆、调用模型、读取 .env / 用户库、发送 QQ 消息、推送或合并 PR。

## 1. 基线、原方案与本轮变更

- 先通过 GitHub API，再实际 git fetch 核对 [PR #10](https://github.com/lsWorl/AICompanion/pull/10)，远程 head 仍为 `37ae7d213a29bdb06ac8a8c0cfe9206b1e9fdadf`，分支 `task/t12-qq-vision-memes`，open、未合并。
- 从该确切提交创建独立分支 `task/t14-smart-expression`，工作区 `C:/Users/27761/.codex/worktrees/t14-smart-expression/AICompanion`。没有使用上一轮含 #11 / #12 的集成分支作为父提交，没有修改 #10 原分支。
- [PR #11](https://github.com/lsWorl/AICompanion/pull/11) 的 T13 head 仍为 `778a8576630a235b08bc249afa578f04ac2ce3c8`，open、未合并；[PR #12](https://github.com/lsWorl/AICompanion/pull/12) head `7d7876585bf476f2667686396d231eca6d98c94b` 同样未合并。
- 已完整阅读实际存在的 `D:/Study/AICompanion/output/T14-smart-expression-design.md`。目标文件原先不存在，现新增本文；原稿逐字节保存为 [历史设计](T14-smart-expression-original-2026-11-08.md)，SHA-256 见本轮验证记录。历史副本不是新的实施指令，冲突时以本文和用户最新要求为准。

| 原设计内容 | 本轮处理 |
| --- | --- |
| 原 ChatController、同次生成、候选检索、资产许可与送达记录 | 保留并明确字段、调用顺序和失败边界 |
| 只支持末尾 sticker 标记、完成后回调一次 | 修订为前置 / 后置标记、请求内流事件与最终结果；前置显示不可倒放已流出的文字 |
| 旧分类协议兼容适配 | 用户本轮追加明确要求直接迁移：取消兼容解析 / 分类选图，不运行旧发送路径 |
| 原图库、用户文件和自定义设置保护 | 保留；迁移资产索引，不删除或覆盖原图 |
| 将本地互动改成在线 LLM 自然发言 | 收紧：不因本任务新增调用；本地台词通过统一结果类型走 text_only，不把 say_line 改成生成入口 |
| 社区知识进一步扩充（原第 10 节） | 移至 T16；T14 只消费既有有界上下文，不新建冲浪或学习机制 |
| 关系硬约束 | 采用未知身份 / 中性关系默认值，不读旧 trust 计算陌生人好感度；关系计算留 T15 |
| 存储和新表建议 | 本轮只冻结接口依赖 / 数据归属，不改表、不确定最终 schema 版本；等 T13 基线确定 |

### 五项长期约束

即使 #12 尚未合并，本设计也遵守用户已确认的原意：角色身份独立于模型；人格独立于记忆；关系独立于渠道但跨渠道识别须身份关联与授权；表达独立于 Agent、切换后保留习惯和关系；用户可创作 / 导入 / 导出 / 切换人格卡，但不能绕过安全边界。新增 asset_id、候选和协议都不依赖模型名或 Agent 实现，不改角色身份或重置共同经历。保留现有聊天卡能力，但不将其误称为完整人格系统或授权身份系统。

## 2. 当前 T12 的真实代码与缺口

以下均已在该工作区读源码，不把计划视为实现。代码路径均相对仓库根。

### 2.1 已有调用链

```text
桌面输入 / 快捷输入 / ASR
  MainWindow.submit → record_user_message → ConversationCoordinator
  → _pump / _start → ChatWorker.run → ChatController.generate
  → ContextService.build → 原 llm_client.generate_reply_stream
  → token_received → _token → CueStream.feed
  → ChatView 文本 + SentenceBuffer / SpeechOutput + 角色动作 / task
  → _result / _sync_delivery（文字送达）

桌面主动问候 / 闲聊 / 心事
  MainWindow._proactive → ProactiveMessageService.due 或 PurposeEngine.due / intent
  → 同一个 _start(proactive=...) → 同一个 generate / UI / TTS

QQ 被叫回复 / 主人私聊 / 允许的陌生人私聊
  NapCat → AstrBot AICompanionBridge.on_message
  → route / stop_event → attach_avatar / attach_images → POST /v1/chat
  → BridgeServer 鉴权 / 体积限制 → BridgeService.handle
  → classify / 额度 / 条数 / 记录 → _reply
  → SocialMemory + 聊天卡 / 世界书 + 社区背景 + QQMedia.offer.prompt
  → 同一 ChatController.generate → finally: strip_cues → reply_filter
  → QQMedia.render → {reply, images} → event.chain_result(Plain + Image)

QQ 自主插话
  handle → _overhear（先记录已允许群的旁听）
  → 原 PROCESS_LOCK / _chime_ok / 预算与频率门槛
  → _reply(chime=True) → 同一大脑和发送路径；可回【不说】保持静默
```

QQ HTTP 收集完流式生成再返回，当前不是 QQ token 流。插件当前使用 `yield event.chain_result`，没有 await 发送后 ACK；不能把 yield 当平台发送成功。`/v1/media-permission` 只做收图 / 头像预检，不是发送许可。`/v1/heartbeat` 返回群列表和媒体设置；目前没有表达版本协商、delivery 领取 / 确认接口。

`ChatController.generate(..., reply_filter=None)` 当前返回字符串迭代器；finally 去 cue，再调用过滤器后写 finish_turn。桌面 `_sync_delivery` 又按听到 / 显示的文字更新同一轮；将附件塞进文字会被覆盖。没有 ExpressionResult / ExpressionStream 或持久附件表。

### 2.2 QQMedia 的现状

- `MemeOffer.prompt` 告知分类，模型可自行决定文字、文字配图或纯图，已经不是必须等用户要求。
- `offer` 读 `qq_meme_root`；无配置时提供仓库 6 类原创姿态；接受分类目录或 `manifest.json + memes/<分类>`。扫描最多 64 项、单类最多 128 项、最终最多 32 类，单图目前不超过 1 MiB。
- `<meme name="分类"/>` 由 `MEME_TAG` 解析；`render` 在分类中 random.choice，校验根目录、体积和真实格式；一轮至多 1 图。
- 冷却键是角色 + channel，构建结果时即更新，早于平台接受。主人 QQ 和桌面共用历史 channel=desktop，因此它不等于真实发送会话键。
- 未提供稳定逐图 ID、前置图片、每图表达意图 / 适用对象、素材许可状态或完整发送确认。
- 收图的 describe / image_bytes / inspect_image 与头像缓存是另一个方向，继续保留。它的既有视觉调用不是“新建选图模型”。不修改只理解表情含义、不分析画面的提示。

### 2.3 所有自然与固定发言入口

| 入口 | 当前机制 | T14 规则 |
| --- | --- | --- |
| 桌面文本 / 语音识别 | 原 _start / generate | 统一同次生成决策 |
| 主人 QQ 私聊 | 原 _reply；历史 channel=desktop | 表达共享；送达 / 去重 scope 按实际 QQ 私聊隔离 |
| QQ 群回复 / 允许陌生人私聊 | 原 handle / _reply | 中性关系候选，保留授权 / 预算 |
| QQ 群主动插话 | _overhear / _chime_ok / _reply | 同一表达逻辑，保留当前是否开口的门槛；不添加表情随机门槛 |
| 早晚 / 闲置问候、心事 / 想关心 | _proactive / 原 due / intent / generate | 主动原因与已有最近上下文参与检索，不增加频率或请求数 |
| 启动 / 降落 / 角色切换问候 | greet / say_line / LinePacer，当前不调用 LLM | 统一 local_result(text_only)，不擅自升级为在线请求 |
| 回到电脑、困了、醒来、戳 / 摸 / 拎起 / 落地 | 状态机 dispatch / _life_tick / say_line | 保留即时动画和节奏；自然本地台词统一纯文字结果，不随机配图 |
| 其他角色 | 同一 controller + 角色包 | 没有对应角色候选就纯文字，不挪用鲸娘身份 |
| 提醒、进度、审批、额度提示、系统错误 | 固定通知 / 工具输出 | 留原通道，不为统一表达新增 LLM 或表情 |

`reply_finished` / `work_finished` 的庆祝等状态效果不得递归制造新生成。本地台词统一结果类型不等于它们开始由模型决策；本阶段能自主配图的主动发言是原来就走 generate 的问候 / 心事 / 群插话。Purpose 的既有目的提炼请求保持原样，不添加选图请求。

### 2.4 关系、习惯、社区的真实边界

`SocialMemory` 仅存 30 天内有限偏好与聚合语气，按角色、channel、sender 的散列隔离；`no_memes` / `more_memes` 不是好感度。`chat_card.prompt` 从有限风格、示例与关键词世界书构造表达参考，忽略外部系统 / 工具 / 历史覆盖字段。`community.prompt` 只取相关且未过期的短期话题；`community_knowledge.available` 提供有来源的词义。

现有主人绑定判断不等于跨渠道人物系统。ContextService 私下仍注入角色级 trust；历史记忆的默认共享语义仍是旧基线。T14 不复用 trust 判断陌生人关系、不扩大 scene / channel / memory 的读取，不把头像或昵称当关联证据。

## 3. 升级架构与唯一责任方

```text
原自然发言入口 → 原权限 / 时机 / 额度 / 取消机制
  → UtteranceContext（冻结历史水位、实际渠道、未知关系、只读社区 / 偏好）
  → ExpressionService.prepare（本地候选 0～10，通常 5～10）
  → 原 ChatController.generate / 原 LLM 请求一次
  → ExpressionStream（只剥离图片控制；不解释动作 / task）
  ├→ 原 CueStream → 可见文字 / TTS / 角色动作 / task
  └→ ExpressionAttachment / ExpressionResult
      → 桌面展示适配器 或 QQ 发送适配器
      → 发送时复核 / 去重 → DeliveryReceipt → 使用历史
```

| 模块 | 保留 | 升级 / 新增 |
| --- | --- | --- |
| controller / client | 同一个控制器、模型、history / prompt、取消、额度 | 新增可选表达请求 / 事件回调，仍 yield str；每轮局部状态 |
| QQMedia | 收图、头像、图片真实格式校验 | 移除发送端分类 prompt / render / random.choice，剩余编码作为适配工具 |
| BridgeService / server | 权限、锁、预算、旁听、历史水位、静默、收图 | 使用统一结果；新增版本 / 短时发送资格 / 确认；不另起大脑 |
| AstrBot 插件 | QQ route / stop_event、图片转换、头像预检、离线不换人格 | 只按决策组装有序消息链并回报平台接受，不选图、不调用 AstrBot LLM |
| 社区 / 聊天卡 / SocialMemory | 现有能力及启停语义 | 提供只读、限定范围的上下文，不增加学习或联网刷新 |
| 桌面 | 原 CueStream / TTS / 动画 / task、既有主动调度 | 新增顺序事件协调、图片 / GIF 展示与附件送达记录 |
| expression（拟新增） | 无现成实现 | domain / stream / service / retrieval / policy / catalog ports，纯 Python 业务与 Qt 分离 |

发送责任唯一：ExpressionService 决定“允许且选中了哪一张”；适配器只执行或拒绝，不能替换图片。QQMedia 不再选图。桌面面板和头顶气泡可作为同一份桌面消息的镜像展示，但共用一个 delivery_id，只计一次，不各自产生发送决策。

## 4. 冻结的数据协议 v1

本节是后续类型实现契约，当前只写文档，不新增 Python 类。除明确说明可变的解析器外，领域快照 / 结果均不可变，集合用 tuple / 只读映射。UTF-8，序列化用 snake_case；未知字段拒绝，版本不认识不尝试猜测。所有 ID 由宿主或资产层创建，模型只能回显候选 asset_id。

### 4.1 基础类型与上下文

- `AssetId = str`：ASCII 小写，正则 `[a-z][a-z0-9_-]{7,79}`；稳定，不含路径、URL、盘符。提供方 + 不变原始标识可散列为 ID；用户本地文件首次索引分配持久 UUID ID。哈希用于内容校验，不冒充原作者 ID。
- `AssetRevision = str`：1～64 个 ASCII 字符的宿主索引版本，不由模型填写。相同资产更换合法内容可在保留 ID 后增 revision；本地同路径的不同内容默认新资产，除非用户明确修订原资产。重命名 / 跨盘迁移不重新分配已索引 ID。
- `Placement = Literal['before_text','after_text']`。
- `ScopeKey`：宿主不透明会话键，包含角色、实际平台、机器人实例和私聊 / 群范围；不把 QQ 的历史 channel=desktop 当实际发送会话。缺乏可信机器人身份时采用请求局部 scope，不与别处合并历史；不能借此推导人物关系。
- `UtteranceContext`：`turn_id:str`、`generation_id:str`、`character_id:str`、`trigger: user_reply|proactive|group_chime|local_line`、`history_channel:str`、`through_id:int|None`、`scene:str`、`delivery_scope:ScopeKey`、`proactive_reason:str=''`、`expected_intents:tuple[str,...]=()`、`relationship:RelationshipSnapshot`、`community:CommunityContext`、`preferences:ExpressionPreferences`、`capabilities:DeliveryCapabilities`、`authorization_epoch:str`、`literal_output:bool=False`。由可信调用侧建立，不能接受模型写入 scope、recipient 或权限。
- `ExpressionPreferences`：`no_memes:bool=False`、`more_memes:bool=False`、`short:bool=False`、`no_questions:bool=False`、`scope:ScopeKey`。只读现有 SocialMemory 的当前合法范围；桌面没有对应偏好则空，不自动把 QQ 偏好跨渠道合并。no_memes 硬禁图；more_memes 只是表达参考。
- `DeliveryCapabilities`：`protocol_version:1`、`supports_images:bool`、`placements:tuple[Placement,...]`、`early_before:bool`。不支持图片就不给候选；桌面可 early_before，QQ buffered 模式为 false。能力是传输能力，不是关系权限。

### 4.2 RelationshipSnapshot（未来关系系统端口）

| 字段 | 类型 / 首版值 | 含义 |
| --- | --- | --- |
| identity_state | unknown / verified；默认 unknown | 身份是否有授权证据 |
| person_ref | str 或 None；默认 None | 未来稳定人物标识，不是昵称、头像或模型猜测 |
| relationship_kind | neutral / family / friend；默认 neutral | 明确证据之外一律中性 |
| intimacy_allowed | bool；默认 false | 表达层可否提供亲密候选，首版不自行授予 |
| link_state | unlinked / authorized；默认 unlinked | 不自动关联平台身份 |
| authorization_ref | str 或 None；默认 None | 后续授权提供方的证据引用，不进模型原文 |
| scope | ScopeKey | 快照仅适用于当前会话 |

T14 首版关系提供方只返回以上中性默认值，不读取 legacy trust / affection，不根据 bridge_owner、聊天卡、发图频率、昵称或头像自动开启亲密权限。已有主人称呼 / 人格上下文不被改写，但也不反向授予亲密图权限。T15 后续提供授权快照仍须经公开群 / 严肃场景过滤，不能只看 intimacy_allowed。没有陌生人打分、关系增长和跨平台自动归一逻辑。

### 4.3 CommunityContext（只读语境）

| 字段 | 类型 / 默认 | 约束 |
| --- | --- | --- |
| community_key | str 或 None；None | 只取现有用户 / 群选择，不靠头像猜社区 |
| scope | ScopeKey | 群选择不影响另一个群 |
| entries | tuple[CommunityEntry,...]；空 | 一次最多 3 条，总内容不超过 1200 字 |
| revision | str；空 | 当前已读取缓存的版本 / 摘要，不刷新数据 |

`CommunityEntry`：`kind: worldbook|lexicon|topic`、`text:str`、`source_url:str|None`、`retrieved_at:str|None`、`expires_at:str|None`。复用 normalize_book、已有关键词匹配、available / 过期判断；既有 prompt 构建与新上下文只能择一注入，避免重复。topic 仅短期背景，不是词义、人物关系或用户事实。T14 构造上下文时不调用 refresh / maybe_refresh，不为桌面新增网络；QQ 原来已存在的刷新调度仍按原设置运行，不扩大范围。原图描述 / 素材文字 / 网页内容一律是数据，不是安全或系统指令。

### 4.4 ExpressionCandidate

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| asset_id / revision | AssetId / AssetRevision | 本轮固定版本引用 |
| character_id | str | 对应表达角色，不按 LLM 名字选图 |
| emotions / intents | tuple[str,...] | 分开记录情绪和沟通意图 |
| summary | str，≤160 字 | 图片表达含义 |
| image_text | str，≤80 字 | 必要图片文字，不是指令 |
| tone_strength | int，0～3 | 强度，不是好感度 |
| audience / scene_tags | tuple[str,...] | 已通过硬过滤的对象 / 场景提示 |
| content_sha256 | 64 位十六进制 str | 发送前校验实际文件，通常不放 prompt |

候选不含本地绝对路径、可供模型下载的 URL、base64 或授权密钥。实际 prompt 只给 ID、简述、意图 / 强度与必要文字；0 张合法。先过滤许可 / 禁用 / 文件 / 角色 / 关系 / 场景，再本地检索 5～10 张，结合最近已送达上下文、主动原因、预期意图、图片语义与会话历史做多样性和重复惩罚。不是把用户关键词映射到必须发送的图，也不把全库放进 prompt。无需网络 / 向量服务 / 选图 LLM；不固定发图比例。

### 4.5 ExpressionDecision

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| action | text_only / text_and_sticker / sticker_only | 程序根据最终可见正文和有效附件派生，不要求模型整段 JSON |
| asset_id | AssetId 或 None | 只能在本轮候选集合中 |
| placement | Placement 或 None | 无图必须 None |
| origin | model_marker / no_marker / rejected_marker / local_text | 不存模型隐藏推理 |
| reason_code | str 或 None | 有界诊断码；不含正文、路径或任意模型理由 |

action 的 text 指剥离图片标记且经原 CueStream / strip_cues 后的非空正文。完整有效的纯图不是空回复失败。图片失效则降为 text_only，保留正文；无正文也无图且没有原有合法 cue / task 载荷才记 empty / failed；不能把原有纯动作或 task-only 回复误判失败。群静默使用 Result.status=suppressed，不增加第四种表达 action。

### 4.6 ExpressionAttachment

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| kind | Literal['sticker'] | 首版最多一张 |
| asset_id / revision | AssetId / AssetRevision | 冻结本轮版本；不随机替换 |
| placement | Placement | 文本前或文本后 |
| delivery_id | str | 宿主生成的一次发送幂等 ID，模型不可指定 |
| media_type | image/png、image/jpeg、image/gif、image/webp | 从实际校验获取 |
| content_sha256 | str | 与固定版本内容匹配 |
| alt_text | str，≤160 字 | 历史中供理解的附件语义，不是用户事实 |

领域附件没有文件路径或 URL。适配器只用受信资产解析端口 resolve(asset_id, revision)，返回有体积 / 格式边界的本地只读内容；实际 QQ 可编码内联数据，桌面可持有已校验图像资源。不能把模型给的字符串直接传给 Path / HTTP 客户端。

### 4.7 ExpressionResult

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| version | Literal[1] | 唯一生效协议 |
| turn_id / generation_id / character_id | str | 原轮次和角色，不用 controller.last_image 全局状态 |
| delivery_scope | ScopeKey | 实际输出范围，不改变历史读权限 |
| text | str | 最终干净正文，无图片控制标记；TTS 仍按原句缓冲 |
| attachments | tuple[ExpressionAttachment,...] | 长度 0 或 1；selected 不代表发送成功 |
| decision | ExpressionDecision | 与正文和附件保持一致 |
| status | complete / cancelled / failed / suppressed | 非 complete 不发尚未投递的图 |
| diagnostics | tuple[str,...] | 有界错误码；不存原始控制段 |

`text_only` 必须无附件且 asset_id/placement 为空；`sticker_only` 必须 text.strip()=='' 且一个有效附件；混合必须有文字和一个附件。cancelled/failed/suppressed 的待发附件清空；此前已实际显示的前置图单独留在送达账本，不重发、不假装撤回。

`DeliveryReceipt` 单独记录 delivery_id、作用域、状态 pending/displayed/accepted/failed/unknown/cancelled、时间和平台回执引用；不是模型输出。平台 accepted 不等于用户已读。文本听到进度、图片可见状态分开，不允许 _sync_delivery 用文字覆盖图片状态。

历史正文保持真实 text，纯表情可以为空；附件另记关联与真实送达状态。ContextService.build 当前会过滤空 content，后续必须只在原已授权 channel / through_id / 删除水位内读取已确认附件，为纯图或混合消息生成简短“角色曾发送的表情含义”上下文；不把空文字附件消息直接丢掉，不把附件说明写成用户事实或提取为长期记忆，不扩大任何 memory scope。原库没有附件的消息按原样处理。

## 5. ExpressionStream 与流式 / 取消契约

### 5.1 新的唯一生成标记

不切换成 JSON 模式，普通 SSE 文字继续走原客户端。合法表情标记独占一行，只允许下列两种位置：

```text
<sticker asset_id="st_0123456789abcdef" position="before_text"/>
好耶，终于做完啦！
```

```text
好耶，终于做完啦！
<sticker asset_id="st_0123456789abcdef" position="after_text"/>
```

没有标记即纯文字；只有合法标记即纯表情，此时 placement 仍保留供统一适配。模型不输出 action / delivery_id / path / URL。before_text 必须是首个非空内容行；after_text 必须为最后非空行。控制行在进入 UI / TTS / 历史前移除；角色的【开心】、<挥手>、<task> 继续交给原 CueStream。所有普通文本、数学 1<2、无关括号必须原样通过。

第一条语法完整的表情声明占用本轮唯一选择槽；不在候选中则本轮无图，不用后续标签“试探”另一张。重复标记只清理并诊断，不执行第二次。before 已经显示，后续 after 不得导致补图或替换。

### 5.2 API 和有界状态机

`ExpressionStream` 每次 generate 独立创建，不能跨轮 / 群共享；它不读数据库、不调用模型、不发送图片。

文档接口：

```python
ExpressionStream(candidate_ids, capabilities)
feed(chunk: str) -> tuple[StreamEvent, ...]
finish(status: str) -> StreamFinal
cancel() -> None
```

`StreamEvent` 为 text（保持原 cue/task）、attachment_proposed 或 diagnostic；带单调 seq。`StreamFinal` 含终态、待验证的选择和诊断；正文以清理后的 text 事件累积，不保存第二份 raw 正文。流式阶段只是 proposal，候选 revision / 文件 / 当前权限仍由服务和适配器复核。

状态：TEXT → POSSIBLE_PREFIX → CONTROL_LINE → TEXT；另有 FENCED_CODE / INLINE_CODE / JSON_LITERAL / TASK_BODY 保护状态和 CANCELLED。保留标记最长 512 字符、前缀识别最多 16 字符；控制段超过上限就丢弃该控制行到下一个换行，随后正文继续输出，不无限缓存、不丢后续正常行。CRLF 按一个边界处理，跨 token 的引号、斜杠、关闭符都测试。

- 未确认是保留前缀的片段，失配后按原文放行；已确认 `<sticker` 的畸形控制行清除，不把 path / URL 泄露为正文。半截标签到 EOF 清除并诊断，保留此前正文。
- 完整控制标签即使误放行内也隐藏，但位置不合法不出图；正常代码或明确引用中的字面标记不执行。
- 代码围栏 / 行内代码 / 普通 JSON 字符串内的字面示例保持原文，不能变成发送命令。task body 原样交给 CueStream，其中的表情写法不在表达层执行；由原 CueStream 消费整个 task，正常情况下不会进入可见正文 / TTS / 历史，不改写任务内容或 task 边界。
- 模型误输出完整顶层表达对象（仅含 action/text/asset_id/placement，首个键为 action）时，最多缓冲 4096 字符，恢复合法 string text 后仅纯文字回退；不据 JSON 发图、不二次修复调用。用户明确要求的 JSON / 协议示例由可信请求标记 literal_output（同时不提供图片候选）或用代码围栏保护，不当隐藏指令。若对象损坏 / 超限，只清理保留协议字段并恢复已能可靠解码的 text，不能宣称可恢复任意损坏 JSON；未知部分不原样泄露控制信息。普通 JSON 不是该保留对象时原样释放。
- after 声明先保存 proposal；若后面又出现正文，则位置无效，正文继续，不发送图。

内部字段冻结：`state:StreamState`、`pending_prefix:str`（≤16）、`control_line:str`（≤512）、`protected_context:代码/引用/task 状态`、`reserved_choice:选择槽或 None`、`seen_visible_text:bool`、`after_has_trailing_text:bool`、`seq:int`、`cancelled:bool`、`diagnostics:tuple[str,...]`。解析器不保存会话全历史；完整生成正文仍归 controller 本轮局部 parts 管理。

`StreamEvent` 字段为 `seq:int`、`kind:text|attachment_proposed|diagnostic`、`text:str=''`、`asset_id:AssetId|None`、`placement:Placement|None`、`diagnostic_code:str|None`；互斥载荷严格校验。`StreamFinal` 字段为 `status`、`selection:(asset_id,placement)|None`、`diagnostics`；正文由已经清理的 text 事件累积，finish 的尾部 text 事件须先 flush，避免重复回放。`finish` 只能执行一次，再调用返回同一终态且不重发事件。

代码 / JSON / task 的保护状态仅用有限前缀与闭合符维护；task 有效正文长度沿用现有 MAX_TASK=1500，超长 / 未闭合任务按原 CueStream 的非任务文本回退，不把它变成新任务，同时清理其中独立的图片控制段。有效 task 的正文与 cue 名称不改写。实现时必须用 S03 的逐字符切块对照原 CueStream，不能凭新增状态机说明宣称兼容。

新增 generate 关键字参数计划为 `expression_context=None, on_expression_event=None`，保留既有位置参数和 str 迭代器；旧调用方没有图片能力时也不把控制标签原样交给 UI。统一清理在 controller 内、yield 之前；history 再使用同一清理结果，禁止重新用 raw parts 覆盖。

解析层的 StreamEvent 不直接交给平台。ExpressionService 持有本轮 candidate 映射，核验 revision / 哈希 / 策略后转换成控制器回调的 `ExpressionEvent`：`kind:attachment_ready|final|diagnostic`、`turn_id`、`generation_id`、`character_id`、`attachment:ExpressionAttachment|None`、`result:ExpressionResult|None`、`diagnostic_code:str|None`；互斥载荷校验。只有 attachment_ready 才可申请展示，不能拿裸 asset_id 跳过服务校验。ChatWorker 将回调与 str token 依发生顺序赋 relay_seq，走同一队列；parser seq 仅用于解析内部顺序，不与 relay_seq 混用。没有回调消费者的调用方按无图能力处理，仍清理控制，不假装图片已送达。最终 Result 复用同一 delivery_id，适配器据账本去重，不能在收到 final 时再次发送已展示的前置图。

### 5.3 桌面前置图与普通流式同时成立

不能等文字流完再把图片插到上方冒充“先发图”。桌面在首行 before proposal 完整且通过本轮候选 / 策略检查时，先发 attachment_proposed 事件，再发正文 token。ChatWorker 在表达模式使用一条按 seq 排序的 Qt 事件通道中转图片提案、文字和最终结果，文字最终仍调用原 _token / CueStream；不让旧 token_received 和新事件同时渲染一份正文。

适配器在 worker 校验内容后、Qt 主线程实际挂载附件前再核对 generation、角色、取消令牌和授权 epoch。正文 / TTS 队列只对这一个待展示前置图短暂等待；最多 1 秒或 4096 字符，先达到就放弃图并立即释放正文。图片超时后不能迟到补发。无图、无效图、text_only / after_text 的正文照常流式，不缓存整轮；主线程不能同步解码大图、等待模型或磁盘扫描。

前置图一旦显示，它已真实送达，即使模型随后异常 / 用户打断也不能假装没显示；记录 displayed，停止尚未显示的正文 / 图。若后续意外出现 task，原任务提示仍按原语义处理，不补图、不递归生成；已展示图保留真实记录。固定工具 / 审批消息本身从不走选图请求。后置图只在生成 complete、原 CueStream.flush 和最终过滤通过后交付。

### 5.4 最终过滤与历史

controller 完成时按顺序：结束 ExpressionStream → 用原 strip_cues 构造正文 → 保留 reply_filter 的原调用签名及 status 语义 → 最终验证结果 / 静默 → finish_turn 写干净正文 → 发 final 事件。过滤器不再做选图 / 编码；QQ 的过滤器只处理群静默等发送策略。过滤器异常时保留已清理正文、拒绝待发图并记录失败，不把 raw 文本或标签写回。

只要提供 reply_filter，统一强制 early_before=false（不猜测任意过滤器是否会抑制；QQ 属于此类），任何图都在最终过滤之后发送；不能边生成边提前发 QQ 图再试图用【不说】撤回。群回复净正文为【不说】或“不说”时整轮 suppressed，即使有合法图也不发送。task 与动作标注仍走原 CueStream，不由 ExpressionStream 执行。自然回复的正常文字解析失败时保留，不重新调用模型。

### 5.5 取消、去重与送达的同步边界

1. 取消、角色切换、退出、服务重启先让 generation / authorization_epoch 失效，再取消 worker；取消是不可逆状态，finish 不能复活附件。
2. Qt 队列、图片解码回调、TTS 队列均带 generation_id；准备发送和执行最后一步时再检查，不只在 token 生成时查一次。
3. QQ 的发送资格在当前权限下短时领取，绑定 delivery_id、原轮次、原固定收件会话和协议版本。领取后未开始发送可撤销；开始发送是本地同步边界，之后平台网络可能已接受，取消只能禁止后续动作，不能保证撤回已经在途的消息。
4. 平台 API 调用异常但无法确认是否送达，记 unknown，不自动重试。ACK 可幂等补报状态，不能幂等承诺第三方平台恰好一次。
5. 同一 delivery_id 只能被一个发送适配器领取一次；主脑锁不持有到外部平台网络返回。角色 / 群 / 实际机器人范围不能由 ACK 改写。
6. 使用历史只在 displayed / accepted 时更新；pending 期间做请求内预约抑制，防并发重复，不把预约当发送成功。scope 隔离各群、QQ 私聊与桌面；冷却后仍可复用。

## 6. 桌面与 QQ 展示 / 发送适配

### 桌面

ChatView.Bubble 和 SpeechBubble 当前只有 QLabel 文本，背景图不算附件。后续添加受限静图 / QMovie 图像容器、尺寸 / 多屏布局、纯图占位清除、暂停 / 释放与历史恢复。动画资源校验 / 缩略图在后台，Qt 对象与展示在主线程。TTS 只接清理后的正文，纯图不合成空语音；Cue 动作词表不改。

before_text 先展示图片再放行文字 / TTS；after_text 在该轮正文完成并完成 cue flush 后追加。TTS 完整朗读时文字完成与听完是不同事件，后置图按最后正文已展示发送，不等同语音已读；语音打断只更新听到的文字，不抹除已显示附件。

### QQ

桥接 v1 的外层保留纯文字 `reply`、`silent`、`reason`，新增 `expression_version:1` 与 `expression`（ExpressionResult 的序列化视图）。附件的已知传输类型 `ExpressionWireAttachment` 在领域附件字段外仅增加 `data_uri:str`，由宿主编码，字段名仍为 `media_type`；不提供本机路径 / 任意下载 URL。`data_uri` 不进入领域对象、历史或模型。保留 1 MiB 单图边界，超限按能力处理为纯文字，不在发送时另随机选图；拒绝无效图时仍保留正文。

新插件确认 v1 后只消费 expression，不再从 reply 二次发送同一正文，也不处理旧 images 字段。未知 / 旧插件仅返回 `{reply: 干净正文, silent, reason}`，无 expression、无 images；这样其现有 plain_result 分支仍能显示文字。保留既有纯文字信封不是兼容旧表情标签或选图路径。

新插件声明 expression_version=1；旧插件或未知版本只收到纯文字，不回退旧表情协议。是否支持前后顺序是能力协商结果，不由 LLM 决定接入版本。建议 /v1/ping 或 heartbeat 公布版本，但权限仍以服务端实时配置为准。

插件将同一结果构造成：before → [Image, Plain]，after → [Plain, Image]，纯图 → [Image]，无图 → [Plain]。需要固定 AstrBot 版本验证 `await event.send(chain_result)` 或等价公开 API 完成后回报 accepted，取代目前的 yield 后即假设成功；准确 API 在 T14-5 用固定依赖验证后实现。本轮没有虚称已调用成功。OneBot GIF 是否以表情气泡显示另做实际平台验收，不能仅凭扩展名或 sub_type 建议宣称成功。

拟新增 `/v1/expression/claim` 与 `/v1/expression/ack`，均沿用 loopback + Bearer、严格大小 / 字段约束，客户端只提交绑定的 opaque delivery_id / 状态，不得指定新收件人或文件。payload 的 self_id / 消息 ID 仅用于传输绑定 / 去重，不成为关系关联授权。未知机器人身份不能污染已有 scope 的使用记录。前置和后置各一图上限不是两图上限：整轮总计 ≤1。

## 7. 旧协议直接迁移（用户最新要求）

### 7.1 不保留运行兼容

新模式没有 `<meme name="分类"/>`→随机图 / 候选图的兼容适配器，也不把旧分类自动解释为新的候选 ID。旧标签在新流中仅作废弃控制内容清理并记 deprecated_marker，保留正文，不发送。它与新标签混在一起时也只执行唯一有效的新声明，不调用 QQMedia.render。

不得同时运行 MemeOffer.prompt 与新候选 prompt、旧 render 与新 adapter、旧 / 新 AstrBot 发图插件的双自动发送。旧 parser / 发送单测中的旧标记样例在切换提交中迁为新资产协议样例；接收图片、权限、预算、自然风格等原断言保留，不通过删除失败测试隐藏功能丢失。

### 7.2 原图库迁移

- 内置 6 张原创图给固定内置 asset_id；角色资源不移动、不改图。
- 用户分类目录 / manifest 只读扫描现有选定根，不扫描任意磁盘。文件首次通过格式 / 哈希检查后创建资产索引，分类描述成为 emotions/intents 的待确认标签，不假装自动理解了精确意图。
- 给用户素材分配持久 ID，保存相对引用、原分类、原始文件标识、内容哈希与 provenance。旧 manifest 和原图不覆盖、不重命名、不删除；新索引放入受管理子目录，重复迁移复用记录。
- 用户自有本地图片可标 user_local_only，在原用户显式选用范围保留本地可用性；作者 / 来源未知不能伪造“已授权公开素材”，不自动上传、提交、导出到正式分享包。来源被禁止或无法排除 Coopanion 衍生时进入待审，不能因“旧图”绕过禁止规则。
- 外部候选只有逐项通过来源 / 许可 / 内容审核才可正式启用。用户原有启停 / 选择不被刷新重置；重名不覆盖，迁移清单独立记录失败和待审核。
- 旧 `[发送表情：分类]` 历史当普通历史说明保留，不反推具体图片、不伪造 delivery_id 或已读。旧 qq_meme_last 仅可作为迁移后初始会话节制提示，不当作某个 asset 的使用记录。

### 7.3 唯一切换和回滚顺序

先在合成环境准备索引与新端到端能力 → 停止旧生成 / 发送并等待在途请求结束 → 原子切换新 prompt / 解析器 / 适配器责任 → 重新建立版本连接。不能在同一在途轮次中途改协议或混合图库版本。

尚未更新的桌面 / 插件只走纯文字，不继续运行旧随机发图。整套旧程序版本可以由负责人明确选择回滚，但先停新实例，保留原图库与备份，不能让新旧自动发送进程并行。回滚不是新代码内置旧协议兼容。涉及 schema 的回滚须用迁移前备份，不能直接让旧程序写新库。真实迁移和升级部署均留后续验收，本轮未执行。

## 8. T13 存储依赖与资产存储端口

本分支确实没有 storage/locations.py、Settings.meme_directory 等 #11 新接口。不能把上一轮本地集成通过当作本分支已含 T13，也不在 T14-0 cherry-pick / merge 它们。

T14-1 启动实际目录写入的门槛：负责人确定 T13 的最终集成版本 / 复用接口；在此之前只可针对合成实现编写资产层，缺失时纯文字并显示可诊断状态，禁止另造根目录或偷偷落 C 盘。

| T13 已有接口 / 设置 | T14 依赖方式 |
| --- | --- |
| Settings.storage_paths_file / database_path / meme_directory / migration_source | 同一份配置与主库，不能新建表情专用会话库 |
| database.get_setting('qq_meme_root') | 保留既有选择；与 T13 设置页现有有效目录解析一致，不擅自覆盖或删除键 |
| directory / default_data_root / record_path | 复用非系统盘验证与推荐策略，不复制一套盘符优先逻辑 |
| read_record / write_record / plan_change | 共用下一次启动路径计划，缓存和索引从所选图库根派生 |
| prepare / copy_database | 复用正式启动时机、SQLite backup / quick_check / 源保留 / 目标冲突保护；不在仍运行的会话移动主库 |
| seed_memes | 兼顾空目录默认素材；不能在非空用户库刷新时强塞默认图 |

T14 的 `AssetStore` 是资产访问端口，不是路径配置系统：

```python
layout() -> AssetLayout
get(asset_id, revision=None) -> AssetRecord | None
search(query, scope, limit) -> tuple[ExpressionCandidate, ...]
resolve(asset_id, revision, expected_sha256) -> ValidatedMedia | None
usage(scope, before_id=None) -> tuple[UsageRecord, ...]
record_delivery(receipt) -> None
```

`AssetLayout`：root（来自 T13）、managed_root、index_location、thumbnail_root、download_cache_root、staging_root；只在所选根内使用独立所有权标记的 aicompanion-library 子目录，不改用户目录结构。目录没有权限 / 冲突就拒绝，不 fallback 到另一个盘。

`AssetRecord` 保留原方案字段：asset_id、revision、相对路径、角色、emotions、intents、semantic、image_text、tone_strength、适用 / 禁用场景、对象、互动方向、author、原始 source / file_id、license / evidence / 适用条件、imported_at、review_status、enabled、sha256 和必要的 original_sha256。所有字段可校验；控制输入只是 asset_id。清理只处理有所有权标记的缓存，不能清理用户原图。

索引 / 缩略图 / 下载缓存的具体物理组织及数据库表迁移在后续实现时定稿；可能沿用原建议的 sticker_assets、sticker_deliveries、message_attachments 归属，但本轮不写 SQL、不推进 schema 版本。外部图片的迁移需另有复制清单、哈希、冲突 / 中断恢复及原文件保留，不能声称 SQLite backup 自动搬迁素材目录。

## 9. 现成方案和素材研究（只参考，不安装 / 复制实现）

| 一手来源 | 可复用思路 | 本项目取舍 |
| --- | --- | --- |
| [AstrBot 表情包管理器开发说明](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/4d2f870e34b54b2c13214e33f9497b71b113344e/DEVELOPMENT.md) | 每回复解析实例、候选复验、取消不补图、成功才记历史 | 借鉴边界和测试，不安装另一套发图决策；其概率 / 辅助选图请求不适合本任务 |
| [该插件 MIT 许可](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/4d2f870e34b54b2c13214e33f9497b71b113344e/LICENSE) | 可评估后续小模块复用 | 当前未复制代码；以后若复制需保留许可与来源，仍须满足本协议 |
| [大肥鱼知识库 README](https://github.com/Loping151/dsh-nekowhale-stickers/blob/b7b58c0ff347d639fabe87d90d6716c4b3cad185/README.md) | 已标注意图 / 场景 / 互动方向，增量同步和用户启停 | 优先复用资料，不运行其 Agent / MCP 或随机选图路径 |
| [素材许可](https://github.com/Loping151/dsh-nekowhale-stickers/blob/b7b58c0ff347d639fabe87d90d6716c4b3cad185/LICENSE-ASSETS) | 知识数据与图片分别审核 | 元数据 CC BY-NC 4.0；站外图片不由代码 Apache 许可覆盖，须保留作者 / 网站并核查具体用途与来源链 |
| [蓝色大肥鱼档案说明](https://github.com/lmy414/bluedafeiyu/blob/main/README.md) | 逐件作者 / 来源 / 许可详情 | 站点代码 MIT 不授予图片使用权；不能整站直接视为可分发图库 |
| [档案站静态 API](https://github.com/lmy414/bluedafeiyu/blob/main/docs/%E9%9D%99%E6%80%81%E5%BC%80%E6%94%BEAPI.md) | revision、稳定作品 ID、分页、逐件 license / image 信息 | 后续导入按 revision 比较清单，只下新增 / 变化，未知授权进待审 |

前两项参考快照为 4d2f870；大肥鱼知识库为 b7b58c0。上游版本仍会变化，后续导入锁定 revision 与许可证据。本轮没有复制上游代码、元数据数据集或原图；归档的只是本项目已有设计。未安装第二个 AstrBot 自动发图插件，未增加运行依赖。

首批 80～120 张的目标继续保留，质量 / 意图多样性优先，不为凑数重复；以大肥鱼为主、少量授权明确且风格相符的可爱图。当前正式新增 / 已逐项许可审核图片均 0；公开元数据条数不是本地正式图库数量。不能确认作者、授权或无法排除 Coopanion / 导出 / 衍生链的图片进入 pending_review，不启用，不把同作者当作来源独立的证据。

导入流程仍沿用原方案：固定来源清单 → 逐项许可 / 禁止来源检查 → 仅下载新增 / 变更到受管理暂存 → 真格式 / 动画完整性 / 体积校验 → SHA-256 去重 → 允许的确定性缩略图 → 原子发布索引 → 新增 / 复用 / 重复 / 失败 / 待审统计。运行候选检索不联网等图，不上传用户聊天检索素材。

## 10. 自动化测试矩阵（待 T14 各阶段实现，非本轮通过记录）

用原模型客户端替身输出不同合法结果，同一个上下文必须可纯文字或配图，不能测试成固定关键词映射。默认最多一图，不测试固定发图比例。测试库 / 图 / 路径均临时合成，不读真实库 / .env，不发真实 QQ。

| 编号 | 输入 / 故障 | 必须断言 | 阶段 |
| --- | --- | --- | --- |
| A01 | 相同提供方 / 原始 ID 重导、跨盘 / 重命名 | asset_id 不变、revision 可变、原图无损、无全量重下 | 1 |
| A02 | 分类目录 / manifest / 内置 6 图迁移 | 索引幂等、保留旧目录 / qq_meme_root / 自定义项、旧 history 不伪造 ID | 1 |
| A03 | 未知许可 / Coopanion 链 / 禁用 / 损坏 / 超限 / 越界路径 | 正式候选排除；记录待审；路径 / URL 不进入模型 | 1～2 |
| A04 | 同名目标、磁盘满、中断、记录失败、缓存清理 | 不覆盖、源保留、可恢复、仅清理自身所有权文件 | 1、6 |
| R01 | 好消息各输出纯文字 / 庆祝图混合 | 同一次 LLM 请求；选择来自候选，允许不同表达 | 2～3 |
| R02 | 笑话输出纯图；连续多轮无图 | sticker_only 合法、无空语音；没有强制比例 | 2～3 |
| R03 | 未知关系、普通群友、群内主人、严肃 / 悲伤 / 冲突 / 紧急 | 亲密和不合适候选被硬过滤；old trust 值不改变结果 | 2 |
| R04 | SocialMemory.no_memes / more_memes、空社区、热榜恶意文本 | no_memes 禁图；不产生好感度 / 用户事实 / 系统覆盖 | 2 |
| R05 | 同图 / 同意图刚用、跨群、冷却后 | 抑制重复、scope 隔离、允许以后复用；不看别群历史 | 2、5 |
| S01 | 每个字符位置切 token，CRLF，前 / 后 / 纯图 | 文本 / 事件顺序一致、最大缓冲有界、最多一图 | 3 |
| S02 | 半标签、超长行、无效 ID、路径 / URL、重复声明、非法位置 | 清理控制、正文保留、无未授权图片 / raw JSON 泄漏 | 3 |
| S03 | 普通 JSON / 代码 / 引用、数学、Cue、跨 token task | 字面正文保留；不执行引用；原 cue / task 语义不变 | 3 |
| S04 | 错误表达 JSON、合法 string text、损坏 / 超限 | 可恢复正文回退、不可恢复不泄露原协议、无第二次请求 | 3 |
| S05 | 前置图显示前取消 / 队列延迟 / 解码超时 | 零发送、正文及时释放、无迟到图、无阻塞 UI | 3～4 |
| S06 | 前置图已显示后取消 / 上游失败 / 意外 task | 已显示账本保留、无后续旧附件、task 仍按原流程 | 3～4 |
| S07 | 后置图生成失败、取消、角色切换、reply_filter 异常 | 纯文字或失败、待图清空、旧 generation 无法复活 | 3～4 |
| D01 | 桌面 before / after / 纯图 / 静图 / GIF / 无图 | 实际展示顺序、TTS 不含控制、占位清理、资源释放 | 4 |
| D02 | 完整朗读时打断、图片已可见 | 只改变已听文字，不覆盖图片送达与历史 | 4 |
| D03 | 主动问候 / 心事、本地 greet / poke / sleepy、reply_finished | 既有主动 LLM 同服务；本地纯文不加调用；不递归、不增加频率 | 4 |
| Q01 | QQ 主人私聊 / 允许群回复 / 自主插话 | 同 controller，一轮新增选图请求为 0；公开权限和预算保留 | 5 |
| Q02 | 群【不说】附图、权限撤销、旧插件、旧标签 | 静默 / 纯文字；无旧随机发送，无双重发送 | 3、5 |
| Q03 | before/after Plain+Image、纯图、平台异常 / unknown、重复 ACK | 正确有序消息链、只发一次、只成功记使用、unknown 不自动重发 | 5 |
| Q04 | claim 改收件人 / 文件 / scope、过期资格、服务重启 | 拒绝；不读取或发送新目标；旧 generation 失效 | 5 |
| V01 | 收到 GIF / WebP 表情、头像、识图关 / 超额 / 未授权 | 保留 T12 接收和自然理解，不增加分析画面回复，不漏原图 | 5～6 |
| X01 | 切换模型 / Agent / 人格卡，迁移路径 | 稳定角色 / asset / 习惯 / 关系 / 记忆不清空；无自动身份关联 | 6 |

既有 test_script、test_chat_card、test_qq_social、test_astrbot_plugin、test_qq_media、test_storage_locations（后者依赖 T13）保留相应断言，发送协议样例直接迁移。本轮只可验证现有行为 / 文档一致性，不能把此表标记已通过。

## 11. T14-1 至 T14-6 的准确实施顺序

每一阶段需登记共享文件负责范围；main_window.py、database.py / schema.py、config.py、pyproject.toml 的变更须先与 Claude 协调。这里是计划，不是假定已获 Claude 同意。没有负责人确认 T14-0 就不进入下一阶段。

| 阶段 | 实施范围与顺序 | 可验收产物 / 进入门槛 |
| --- | --- | --- |
| T14-1 资产与存储基础 | 先确定 T13 接口基线；实现本文领域类型与 AssetStore 端口、稳定 ID、许可 / 增量导入 / 去重、旧图库索引迁移和合成恢复；需要的 schema 由唯一负责方推进 | 不接聊天；根路径只来自 T13；原素材 / 设置不损；待审和启用数真实可核对 |
| T14-2 上下文与候选 | 中性关系 / 空社区 provider、只读 SocialMemory、场景 / 对象硬过滤、本地 0～10 候选与会话重复抑制 | 不做 T15 / T16，无模型决策请求；合成关系 / 场景矩阵通过 |
| T14-3 协议与原控制器 | ExpressionStream、同次生成 prompt / 事件、原 cue/task/过滤 / 历史协调、取消注册和送达端口；同一切换中移除旧发送 prompt/render 责任 | 新旧不并行；尚无新适配器的渠道暂只纯文字；旧标记不再发图；分块 / 失败 / 取消 / 请求数测试通过 |
| T14-4 桌面接入 | ChatWorker 顺序事件、MainWindow / Bubble 图片适配、TTS / 附件送达与历史恢复；原主动入口同服务，本地台词 text_only | 桌面前 / 后 / 纯图实际可见；无新增调度 / 调用；Smoke 含取消和资源释放 |
| T14-5 QQ 接入 | 新版本协商、统一结果 / 消息链、短时资格与 ACK、固定 API 校验；移除插件旧发送字段路径，保留收图 / 头像 / 静默 / 预算 | 模拟 / 本地 HTTP 先通过；旧插件纯文字；私聊 / 群 / 插话发送适配完成，不自动发真实测试群 |
| T14-6 管理、素材与整体验收 | 图库 UI 预览 / 来源许可 / 启停 / 索引刷新 / 空间 / 缓存清理，大型外部资源迁移压测；逐项审核 80～120 目标；全回归与独立授权的真实模型 / QQ 验收 | 明确数量 / 许可 / 待审；Windows 截图与实测；Claude 复审、负责人验收，未通过项不标完成 |

阶段 1 的 schema / 对外素材写入要等 T13 确认；阶段 3 到 5 之间允许部分渠道只纯文字，但不允许回退旧随机图。部署新协议必须等待同一渠道两端就绪，不能把开发中间态交给真实账号使用。T14-6 的真实调用 / QQ 测试另取明确授权，只用约定测试对象；单次凭据授权不沿用，临时模型脚本用完删除。

### 后续文件范围（当前均未修改）

- `expression/domain.py, stream.py, service.py, catalog.py, retrieval.py, policy.py, importer.py`：新类型、本地资产 / 候选与解析，按阶段 1～3 依赖顺序实现。
- `controllers/chat_controller.py`：原 generate 的可选上下文 / 事件、历史清理与结束协调；不另起 LLM 入口。`services/context_service.py` 仅在阶段 4 为原授权消息读取已确认附件语义，保留 channel / 水位 / 删除规则，不扩展记忆范围。
- `ui/chat_worker.py, main_window.py, chat_view.py, pet_bubble.py, desktop_pet.py`：有序事件、附件 / TTS、已有主动和本地台词结果；阶段 4，main_window 先约定负责范围。
- `bridge/media.py, service.py, server.py` 与 `integrations/astrbot_plugin_aicompanion/main.py`：阶段 3 移除旧选图责任、阶段 5 接新发送；保留所有收图 / 权限 / 预算。
- `storage/database.py, schema.py` 及依赖 T13 的 `storage/locations.py, ui/storage_page.py`：只按已确认的主库存储接口推进迁移 / 附件，不新增根配置。单一负责方协调后才能改。
- 图库管理页、settings_window、导入工具、相关 tests / pet_smoke_check：随阶段 1 / 6 扩展。T14 不计划改 agent/、角色主定义或增加运行依赖；若依赖变更另行说明锁版本。

## 12. 本轮交付与未实现清单

交付：本文、原设计完整副本、tasks 任务行和 implementation_status 的仅文档记录；本地验证记录另存 output。没有创建 expression 运行模块、迁移 SQL、新测试文件、下载工具或新图片。

尚未实现：本文全部新类型 / parser / candidate 服务、桌面附件、QQ 新协议与发送确认、稳定图库索引 / 导入 / 管理 / 大型迁移。T13 也没有被并入本分支。本轮不把先前集成区的 407 项结果或历史真实模型 / QQ 结果冒充本轮新功能通过。

本轮实际执行项目、基线 / 文档校验结果、最终本地提交见 `output/T14-0-design-review-2026-11-08.md`。本轮不新增 PR 评论、不推送、不创建 PR、不合并；完成后停止等待项目负责人审核。