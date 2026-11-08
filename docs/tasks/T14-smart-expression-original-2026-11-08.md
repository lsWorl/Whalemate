# T14 大肥鱼智能表情表达系统：代码检查与待确认设计

日期：2026-11-08（Asia/Hong_Kong）。负责人建议：Codex；Claude 复审。

本轮为设计检查，不是实现交付。检查基于 `task/t12-qq-vision-memes` 的 `37ae7d213a29bdb06ac8a8c0cfe9206b1e9fdadf`。当前工作树干净。PR #10、#11 均仍打开；没有下载图片、扩大正式图库、读取真实聊天库或 `.env`，没有修改运行代码或发布 PR 评论。

## 1. 已核实的现状

### 1.1 消息生成与发送调用链

桌面普通消息：

```text
聊天输入 / 桌宠快捷输入 / ASR 识别
 → MainWindow.submit
 → record_user_message + ConversationCoordinator 合并 / 打断
 → MainWindow._pump → _start → ChatWorker.run
 → ChatController.generate
 → ContextService.build（原角色、记忆、关系、会话历史）
 → 原 llm_client.generate_reply_stream（SSE）
 → MainWindow._token → CueStream
 → ChatView 文本气泡 + SentenceBuffer → SpeechOutput
 → MainWindow._result / _sync_delivery → 历史与送达状态
```

桌面由心事或调度触发的主动聊天：

```text
既有每分钟 _proactive
 → PurposeEngine.due / ProactiveMessageService.due
 → _start(proactive=意图)
 → 同一个 ChatWorker → ChatController.generate → 原流式、语音和气泡
```

QQ 主人私聊、允许群中的被叫回复：

```text
NapCat → AstrBot → AICompanionBridge.on_message
 → 原有头像预检 / 图片转换 → POST /v1/chat
 → BridgeService.handle（权限、额度、会话和原图理解）
 → _reply → QQMedia.offer（分类）
 → 同一个 ChatController.generate
 → reply_filter → QQMedia.render
 → HTTP {reply, images} → AstrBot Plain + Image → NapCat → QQ
```

QQ 主动插话：

```text
允许群的新旁听消息
 → _overhear（记录旁听，不等忙碌的大脑）
 → 既有插话门槛 / 冷却 / 条数 / 公开预算
 → _reply(chime=True) → 同一大脑与 QQ 发送链路
```

QQ 桥接目前在服务端收集流式模型结果，然后一次返回 HTTP JSON；不是逐 token 发给 QQ。桌面原有流式输出需要继续保留。QQ 目前没有“完全没有新消息时，由独立 QQ 定时器发信”的实现；本次统一已有主动发言入口，不借表情功能增加调度器或群消息频率。

### 1.2 所有自然发言入口

| 入口 | 当前路径 | 拟处理方式 |
| --- | --- | --- |
| 桌面输入、快捷输入、语音识别 | submit → _start → generate | 统一表达；ASR 不新建另一条决策链 |
| QQ 主人私聊 | handle → _reply → generate；上下文仍为 desktop 私下频道 | 统一表达；发送统计按实际 QQ 会话区分 |
| QQ 群被叫 / 名字触发回复 | handle → _reply → generate | 统一表达，普通群友不取得父女关系 |
| 已允许的陌生人 QQ 私聊 | classify → _reply → generate | 统一表达，陌生人关系策略 |
| QQ 旁听后的自主插话 | _overhear → 原有门槛 → _reply | 统一表达；保持已有是否开口机制，表情不另加概率 |
| 早晚问候、长时间未聊后的问候 | _proactive → ProactiveMessageService.due | 同一个 generate，检索纳入主动原因和最近对话 |
| 心事 / 想分享 / 想关心 / 想请求 | _proactive → PurposeEngine.due / intent | 同一个 generate，保留来源记忆、时机和每日上限 |
| 启动、降落后、切换角色的问候 | drop_in / _switch_to → DesktopPetWindow.greet → say_line | 自然问候进入统一发言请求；不增加触发次数 |
| 回到电脑、困了、醒来 | _life_tick / 状态效果 → say_line | 自然内容进入统一请求，保留 LinePacer；离线走纯文字本地回退 |
| 戳、摸头、被拎起、落地等互动台词 | dispatch → say_line | 动画立即执行；自然话语经现有节奏门槛后异步进入统一请求 |
| 其他角色自然聊天 | 同一 controller，角色包决定人格 | 共用表达机制，但默认没有鲸娘图片候选，不挪用大肥鱼身份 |
| 提醒、额度不足、工作进度、审批、错误与状态 | show_bubble / ask / agent_session 等 | 事务消息；保持本地 / 原任务输出，不新增 LLM 或贴图 |

`PurposeEngine` 的目的提炼目前会有单独的原有后台模型调用；它不是一次自然聊天或表情决策。这部分保留，不增加“选表情”调用。

需专门防止的循环：`reply_finished`、`work_finished` 也可能产生 `celebrate` 等状态效果。它们继续只驱动动画 / 事务提示，不触发新的自然聊天；本轮庆祝意图应随当前发言一起组织。欢迎回来与唤醒等同一物理事件合并成一个发言机会，不能连续触发两次生成。

### 1.3 当前图库和解析机制

- 默认只有 6 类、每类 1 张仓库原创鲸娘姿态。自定义图库兼容 `manifest.json + memes/<分类>` 或普通分类目录。
- 大脑输出 `<meme name="分类"/>`；`QQMedia.render` 在分类内随机取图，没有逐图 asset_id、表达意图、对象限制或语义去重。
- 现有会话冷却在构建发送结果时更新，早于 QQ 实际发送；需要区分“已选中”与“平台已接受”。
- 桌面 `ChatView.Bubble`、`SpeechBubble` 目前是文字 QLabel，没有消息图片 / GIF 显示。聊天面板已有的背景图片不算消息附件支持。
- `CueStream` 处理角色动作 / 表情和 `<task>`。只读分片实验确认：现有解析器会原样输出 `太好了！<meme name="happy"/>`；不能直接把新图片标记交给它。
- `ChatController.generate` 的 finally 与桌面 `_sync_delivery` 都会触及历史。新图片附件必须独立保存，避免语音打断时覆盖图片送达记录。
- 接收图片已经走单独的视觉理解路径；该能力保留，其额外视觉请求不是发送表情的决策请求。

### 1.4 非 C 盘存储的实际依赖

PR #11（T13，`778a8576630a235b08bc249afa578f04ac2ce3c8`）仍未合入，当前 T12 分支没有 `storage/locations.py`。

已检查 T13 源码：设置支持 memory_root / meme_root、拒绝系统盘，记忆库下次启动复制并保留旧库；图库目前是切换目录或向空目录填入默认图，尚无“大型图库连同索引 / 缩略图复制迁移”。后者需要在本任务补齐，不能当作现有能力。

实施应以 PR #10 / #11 的最终审查版本为明确依赖；先与 Claude 约定 `ui/main_window.py`、`storage/database.py`、`storage/schema.py` 的负责范围。不能另写一套路径配置。

## 2. 统一表达架构

```text
原有自然发言触发器（输入 / 主动意图 / 互动）
 → 原调度、权限、安静时段、额度和忙碌检查
 → UtteranceContext：冻结角色、轮次、关系、场景和上下文水位
 → ExpressionService.prepare：本地筛选最多 10 张候选
 → ChatController.generate：原 LLM 一次流式生成文字与图片标记
 → ExpressionStream：先隐藏图片控制信息，再交原 CueStream 处理角色动作
 → ExpressionDecision：text_only / text_and_sticker / sticker_only
 → 桌面或 QQ 发送适配器
 → 确认展示 / 平台接受 → 记录附件与使用历史
```

统一点放在原 `ChatController.generate`，不是仅放在 `_reply`。桌面与 QQ 共用服务；桥接保留权限、额度、场景隔离与收图能力，移除其独立的随机选图决策。

对外兼容保留 `generate` 的字符串迭代器，拟新增可选的 `expression_context` / `on_expression` 参数：每个轮次持有自己的候选和决策，完成时回调一次。ChatWorker 把回调变成携带 generation_id 的 Qt 信号，QQ 适配器在本次请求局部收集结果；不在 controller 上存跨线程共享的“最后一张图”。没有图片发送能力的旧调用方使用 text_only，不能记录实际上未显示的附件。

普通生成仍可输出纯文字。每次自然发言的表达决策新增 LLM 请求数为 0。一次最多一张表情；固定模板状态不因启用图库而进入模型。

本地离线台词通过同一表达结果类型以 text_only 交付；不在离线模式伪称模型选了图。在线自然互动保留原动画时效，用后台生成话语；忙碌时不抢断用户正在进行的对话、不排队补发一串旧互动。

## 3. 候选检索与关系策略

### 3.1 检索输入

- 当前会话最近若干轮已送达的对话，而非仅当前用户一句话。
- 角色人格、当前关系、实际发送场景与聊天氛围。
- 主动发言时的原因、心事 / 来源记忆、预期沟通目的。
- 图片语义、文字、情绪与表达意图、最近图片 / 同义意图的使用历史。

回复场景尚不知道最终表达意图时，保留多种合理意图候选，不能先把用户情绪等同于她的表达。比如好消息可同时提供庆祝、认同、俏皮得意、温和鼓励，最后由大脑组织发言并决定是否出图。

### 3.2 本地算法

首版使用小型本地文本索引 / 中文片段匹配、字段加权、意图多样性与历史惩罚，不新增向量服务或模型。先硬过滤未审核、禁用、丢失、角色不符、对象不符和场景不适合的图片，再取 5～10 张；不足时提供实际合格数量，允许 0 张。

候选提供稳定 asset_id、简短语义、图片文字、意图与语气 / 对象提示；不把全库、真实文件路径、来源许可长文塞进 Prompt。大脑可以忽略全部候选。

### 3.3 硬约束

- 家庭式亲近与恋爱互动分开。父女关系允许合适的撒娇、关心，不因“主人”身份而自动放开恋爱、挑逗或亲吻图。
- 普通群友默认没有抱抱 / 撒娇等亲密候选；群内爹也受公开场景约束。
- 严肃、悲伤、冲突、紧急事务默认纯文字或极少数合适的支持性候选；不提供嘲笑、起哄、炫耀图。
- 发送前再核验本轮候选集合、当前启用 / 授权状态、路径与真实文件；不能临时随机换成另一张。
- 图片使用历史按角色 + 实际平台 / 会话区分，不能因为 QQ 主人沿用 desktop 上下文就污染各群的计数。
- 近期同图与同义意图降低优先级或短暂排除，冷却后可再次使用，不规定发送比例、不用随机概率决定发不发表情。
- 群沉默优先：即使【不说】旁边混入图片标记也整轮不发送；保留原有静默语义。

## 4. 流式协议与失败回退

### 4.1 拟定协议

保持普通 SSE 文本，约定可选的末尾控制标记：

```text
今天可算忙完了，爹快歇会儿。<sticker asset_id="whale_a1b2c3d4"/>
```

只输出文字即 text_only；只有有效图片标记即 sticker_only。三个 action 是程序解析后的统一对象，不要求模型将整段回复改成 JSON。角色原有【开心】、动作和 `<task>` 标记继续使用原词表与处理机制。

### 4.2 解析与交付顺序

1. `ExpressionStream` 在 controller 内消费原始 token，只缓存可能属于保留控制标记的短前缀，普通文字继续往外流。
2. 图片控制信息在进入 UI、SentenceBuffer、TTS、历史前就隐藏；完整结束后验证 ID 必须在本轮候选中。
3. 桌面通过带 generation_id 的表达完成事件传递图片，等待该轮最后文字 / CueStream flush 完成后追加；纯表情不会保留“正在想”占位或触发空语音。
4. QQ 继续用 Plain + Image 消息链发送文本与至多一图；不为表情改变 QQ 的现有请求 / 回复机制。GIF 的“表情”呈现参数需要在固定 AstrBot / NapCat 版本实测，不把普通图片消息误称为已验证的原生表情显示。
5. 角色切换、打断、失败、旧 generation_id 一律丢弃尚未发送的图；已经显示 / 发出的图保留真实送达记录。

### 4.3 异常情况

- 标签跨 token、多个标签、半截标签、过长控制段：有界状态机处理；保留合法正文、去掉控制段，不发送无效图片。
- 无效 ID、文件丢失、临时禁用：保留正文，回退 text_only；只有图片而没有可用文字时给简短本地发送失败提示，不把空消息当成功。
- 模型误输出表达 JSON：开头检测已知表达对象并有界缓存；仅解析白名单 action / text / asset_id。合法文字可恢复，失败时不展示协议 JSON，也不二次调用模型“修复”。普通用户要求的 JSON 正文与表达协议对象要区分。
- 旧 `<meme name="分类"/>` 在兼容期识别并隐藏，保留正文回退，不再据类别随机选图；旧目录通过图库适配转换为逐图候选，旧数据不因此被删除。
- `<task>` / 工具事务和审批输出优先沿用原流程，不为任务确认额外发图。

### 4.4 送达与去重

图片从 selected / pending 到 displayed（桌面）或 accepted（QQ 平台 API 无错误返回）才计作已使用。QQ 采用固定版本已核实的 `await event.send(...)` 适配点，再回报带 delivery_id 的本机确认；不能把生成 HTTP 成功当作 QQ 发送成功。

确认与重试必须幂等，发送失败与超时可记录 failed / unknown；unknown 不盲目重发以免重复。平台接口接受不等于对方已读。权限在发送适配器执行时再次核验；撤销群权限后的待发送结果作废。

QQ 发送前通过原授权本机接口领取绑定 delivery_id、原轮次与原会话的短时发送资格，核对当前群 / 主人设置；完成后报告结果。接口不允许客户端指定新收件人或新文件。资格领取是最终授权检查边界，平台已接受的消息无法靠本机撤销；不持有大脑锁等待外部平台网络返回。

## 5. 图库来源、许可与导入

### 5.1 已核实来源

Loping151 的公开元数据当前有 322 条：286 条单人大肥鱼、36 条双人图；标注包含 id、12 位上游 hash、text、emotion、tags、use、desc、meme、dir。

逐图数据没有独立 author / license 字段，因此导入时必须把全局作者及适用的许可证据展开到每条记录，另核查原图出处与禁止来源链。不能直接把元数据条数计作已获准图库数量，也不能把其 12 位 hash 当作本地完整 SHA-256。

其 LICENSE-ASSETS 区分 CC BY-NC 4.0 知识数据与站外 GIF，并说明站外素材在保留原作者链接 / 网站的非商业条件下分享。归档站明确：代码 MIT 不自动覆盖作品图片，需看单件详情来源与授权。

首批优先单人大肥鱼；补充图逐项审核，目标正式启用 80～120 张。授权或来源链不明、无法排除 Coopanion / 导出 / 衍生来源者进入 pending_review，不能正式启用。可爱家庭式互动与恋爱双人图分开，不能为凑数收录重复或不合适素材。

本轮只读了清单 / 许可 / 文档，没有下载原图或完成逐张视觉筛选。正式新增图片数为 0。

参考记录：

- https://github.com/Loping151/dsh-nekowhale-stickers
- https://github.com/Loping151/dsh-nekowhale-stickers/blob/main/LICENSE-ASSETS
- https://github.com/Loping151/dsh-nekowhale-stickers/blob/main/meta/nekowhale.json
- https://github.com/lmy414/bluedafeiyu
- https://github.com/lmy414/bluedafeiyu/blob/main/README.md
- 元数据 Git blob：4b3c93dec5c98b6d9461148e6795b48e3077f65f
- 许可 Git blob：cbbb5c19d3893dd8976d6206868de7f69d2a1560

### 5.2 数据结构

`StickerAsset` 至少包含：

```json
{
  "asset_id": "whale_<provider_and_original_id_digest>",
  "relative_path": "assets/<pack>/<asset_id>.gif",
  "thumbnail_path": "thumbnails/<asset_id>.webp",
  "character_type": "deepseek-whale",
  "emotions": ["开心"],
  "intents": ["庆祝", "鼓励"],
  "semantic": "为对方完成一件事而开心",
  "image_text": "",
  "tone_strength": 2,
  "allowed_scenes": ["owner_desktop", "owner_qq", "group_reply"],
  "blocked_scenes": ["conflict", "emergency"],
  "audiences": ["owner_family", "ordinary_friend"],
  "interaction_direction": "self_to_other",
  "relationship_style": "neutral",
  "author": {"name": "原作者", "url": "作者页"},
  "source": {"provider": "来源提供方", "url": "原图 / 作品页", "original_file_id": "上游原始标识", "revision": "来源版本"},
  "license": {"name": "已核实的适用条件", "evidence_url": "证据页", "evidence_revision": "证据版本", "conditions": []},
  "provenance_status": "verified",
  "review_status": "approved",
  "imported_at": "ISO 时间",
  "enabled": true,
  "sha256": "本地完整文件哈希",
  "original_sha256": "若有转码则同时保存原图哈希"
}
```

asset_id 由提供方与原始文件标识确定，图片更新保持 ID 并生成新 revision；文件哈希单独校验。元数据和原图文字仅作为素材，不作为指令。

`UtteranceContext`：turn_id、generation_id、character_id、trigger、原上下文 channel、水位、实际发送 platform / session、关系、场景、proactive_reason / intent、策略快照。

`ExpressionDecision`：action、text、asset_id、delivery_id；asset_id 可为空且最多一个。

### 5.3 可重复导入

```text
固定来源版本 / 清单
 → 每项许可与来源审核（待审核单列）
 → 比较本地清单 / revision，只下载新增或变化的文件
 → 临时目录写入 → 校验真实格式、所有动画帧 / 帧数预算、尺寸与体积
 → SHA-256 去重 + 视觉近似重复提示
 → 必要且允许的确定性尺寸 / GIF 转码，保留原始标识与衍生关系
 → 缩略图与元数据校验
 → 原子发布资产 / 清单 → 更新本地索引
 → 输出新增、复用、重复、失败、待审核和正式启用数量 / 空间 / 许可统计
```

索引刷新只扫描本地变化，不重新下载图库。运行时候选检索不联网等图片，不给原模型发送任意下载 / 文件读取能力。图片解码与导入在后台执行，Qt 主线程只做预览与显示。

## 6. 存储、表结构与管理

实际目录以 T13 的 meme_root / qq_meme_root 为准，不硬编码 D，也不退回 C 盘缓存：

```text
<用户选择的非系统盘图库根>/aicompanion-library/
  manifest.json
  assets/<pack>/...
  thumbnails/...
  cache/downloads/...
  staging/...
  review/pending.json
```

现有分类图库保留，通过适配器导入稳定 ID。自己的缓存、暂存和缩略图有独立所有权标记，清理缓存只处理这些目录，不动用户原图。

复用当前记忆数据库，不建立第二套会话库。建议在 schema v5 之后由 Codex 独占实现一次新迁移（最终版本号以集成基线为准）：

| 表 | 用途 |
| --- | --- |
| sticker_assets | 元数据索引、审核与启用状态、相对路径及 revision；可由清单恢复，个人启用选择不被刷新覆盖 |
| sticker_deliveries | 轮次 / 角色 / 实际会话、选图意图、pending / accepted / displayed / failed / unknown、时间；作为去重依据 |
| message_attachments | 聊天消息 / turn_id 与 asset_id 的关联、已确认附件状态，供历史渲染与模型上下文读取 |

主聊天内容继续保存实际文字。已确认附件给大脑一个短语义说明；不保存 base64、绝对路径或原始控制标签，不把素材说明抽取成用户事实。纯表情也有合法消息记录；语音打断只改变已听文字，不能抹掉已看到的图片附件。

设置页增加图库管理：缩略图网格、语义 / 文字 / 对象 / 作者 / 来源 / 许可详情、单图启停、待审核列表、刷新本地索引、占用空间和清理下载缓存。

路径变更区分“选用另一图库”与“迁移当前图库”。迁移当前库使用相对路径、复制 / 哈希验证、原子切换路径，保留源资源；失败继续旧库，既有目标不覆盖。复用 T13 的下次启动迁移时机，避免正在生成 / 播放图片时切换根目录。

## 7. 计划文件修改范围

以下均为计划，尚未修改：

| 文件或目录 | 修改目的 |
| --- | --- |
| D:/Study/AICompanion/src/aicompanion/controllers/chat_controller.py | 所有自然发言共用表达准备 / 流式解析 / 完成事件；不换 LLM 客户端 |
| D:/Study/AICompanion/src/aicompanion/services/context_service.py | 共用当前已授权上下文；纳入附件语义；保留 private / public 隔离 |
| D:/Study/AICompanion/src/aicompanion/expression/（新增） | domain.py、catalog.py、retrieval.py、policy.py、stream.py、service.py、importer.py；纯逻辑优先不依赖 Qt |
| D:/Study/AICompanion/src/aicompanion/ui/chat_worker.py | 传递冻结的发言上下文与带轮次的表达完成事件 |
| D:/Study/AICompanion/src/aicompanion/ui/main_window.py | 自然入口汇合、打断 / 切换 / 送达协调、消息附件历史；须先与 Claude 约定 |
| D:/Study/AICompanion/src/aicompanion/ui/desktop_pet.py | 自然互动 / 问候转发言意图，动画与状态保持独立，避免 reply_finished 再触发聊天 |
| D:/Study/AICompanion/src/aicompanion/ui/chat_view.py | 同一消息内显示文字 / 静图 / GIF，纯表情与历史恢复 |
| D:/Study/AICompanion/src/aicompanion/ui/pet_bubble.py | 桌宠气泡附件、尺寸 / 多屏 / 淡出 / 清理，不更改角色形象渲染器 |
| D:/Study/AICompanion/src/aicompanion/ui/library_page.py（新增） | 图库管理及许可 / 来源预览 |
| D:/Study/AICompanion/src/aicompanion/ui/settings_window.py | 挂载管理页；保留现有设置项 |
| D:/Study/AICompanion/src/aicompanion/bridge/service.py | 使用 controller 统一表达；移除 QQ 独立随机选图，保留权限 / 预算 / 静默 |
| D:/Study/AICompanion/src/aicompanion/bridge/media.py | 保留接收识图 / 头像；发送改为已决策资产的校验与编码 |
| D:/Study/AICompanion/src/aicompanion/bridge/server.py | 在原本机授权接口内增加幂等发送确认，不开放任意文件或发信目标 |
| D:/Study/AICompanion/integrations/astrbot_plugin_aicompanion/main.py | 发送统一消息链与确认结果；收图流程保持 |
| D:/Study/AICompanion/src/aicompanion/storage/schema.py | 新元数据 / 发送历史 / 消息附件表与索引，版本由唯一负责方推进 |
| D:/Study/AICompanion/src/aicompanion/storage/database.py | 新表访问与事务、已送达附件和文字兼容；须先与 Claude 约定 |
| D:/Study/AICompanion/src/aicompanion/storage/locations.py（依赖 PR #11） | 图库复制迁移、缓存 / 缩略图路径复用 |
| D:/Study/AICompanion/src/aicompanion/ui/storage_page.py（依赖 PR #11） | 区分迁移当前库与选择其他库 |
| D:/Study/AICompanion/tools/import_sticker_library.py（新增） | 正式可重复的导入工具与统计，不是一次性模型测试脚本 |
| D:/Study/AICompanion/tests/、src/aicompanion/ui/pet_smoke_check.py | 全入口、流式、许可、迁移和真实 Qt 显示回归 |
| D:/Study/AICompanion/docs/tasks.md、docs/tasks/T14-smart-expression.md、docs/implementation_status.md | 实施状态、验收、许可统计与限制 |

首版不计划新增运行依赖、修改角色主定义或 agent/。若现有 Qt 不能完成所需 GIF 优化，先评估已用于美术工具的 Pillow；运行依赖与锁版本调整需单列理由并协调 pyproject.toml。

## 8. 实施顺序与验收

确认设计后，再登记 T14、约定共享文件负责方，并建立 `task/t14-smart-expression`。PR #10 / #11 先解决依赖集成，不直接覆盖任一分支。

1. 图库模型、来源审核与增量导入；产出实际许可清单、待审核列表和正式图库统计。
2. 统一 ExpressionService、候选策略和流式协议；先证明纯文字 / 纯表情 / 混合不增模型请求且不泄露控制信息。
3. 桌面所有主动 / 被动入口、图片气泡与历史；同一控制器接 QQ，补送达确认。
4. 图库管理、非系统盘缓存与复制迁移；清理仅自己的缓存。
5. Windows 自动化、真实模型抽样和 QQ 实际发送验收，交 Claude 复审；用户最终合并。

自动化必须覆盖：

- 同一个好消息可以 text_only 或 text_and_sticker，笑话可 sticker_only；结果由模型输出决定，而不是测试中写死情绪映射。
- 普通输入 / ASR / QQ 主人 / 群回复 / 群插话 / 定时问候 / 心事 / 本地自然互动均调用统一服务；每次发言只调用一次原生成客户端。
- 发言事件不被表情引擎新增；保留 LinePacer、静音 / 专注、Purpose 限额、群冷却和预算，回复结束不递归触发。
- 普通群友无亲密候选，公开群不能带入 private 记忆；悲伤 / 冲突 / 紧急场景没有不适合的图。
- token 每个位置分割、半标签、超长标签、错误 JSON、无效 ID、多标签、角色切换和取消；UI / TTS / 历史无控制泄漏。
- 纯表情不误判空回复；丢文件、禁用、许可撤回、编码失败保持纯文字回退；群沉默不因附带标签而发图。
- 同图 / 同义图抑制、冷却后可复用、跨群隔离、发送失败 / unknown 不冒称成功；确认幂等。
- 不确定许可只进待审核，来源禁止清单、真实文件哈希与动画完整性、重复导入不重下、索引刷新不重置启停。
- 临时合成库覆盖跨目录复制、失败恢复、目标冲突、缓存范围和纯表情历史恢复。

现场验收分开记录：桌面图片 / GIF 实际显示和打断，QQ 私聊纯表情 / 混合，允许群的回复 / 主动插话，已有调度触发且没有新用户消息时的桌面主动问候 / 心事。真实 QQ 的角色关系、消息顺序和 GIF 呈现不能用离线桩代替。

按项目要求运行 `tools/run_checks.bat`，并增加对应自检。真实模型只用临时数据，临时调用脚本结束后删除；不得复用之前已经结束的 `.env` 单次授权。真实账号消息发送测试需约定测试对象，不由后台擅自给群发测试消息。

最终交付明确列出：修改文件、图库及许可数量、所有自然入口到同一表达服务的调用链、截图 / 真实发送记录、自动化结果、尚未完成或仅预留的能力。独立 QQ 无消息定时广播、第二个 Agent / 决策模型不在本次设计内。

## 9. 本轮检查结果与限制

- 已完成：相关代码入口、流式与语音、消息历史、原图库、QQ 发送、主动调度、T13 存储源码、两个上游的元数据 / 许可检查。
- 已运行：现有 CueStream 分片只读实验，证实直接复用会泄露旧图片标记；未改变代码。
- 尚未实现：新系统所有模块、逐图下载 / 审核、80～120 张正式图库、桌面发图、增量导入与真实 QQ 新协议验收。
- 当前正式新增图库数量：0；查阅的 322 条上游标注不是已导入数量。本轮没有调用模型或读取私人数据。

## 10. 延续上一轮的社区知识扩充要求

上一轮“扩充知识，不只获取热点”的要求继续保留。它是资料整理与相关知识检索，不是另一个自然聊天入口或表情决策模型：

- 扩展现有 `D:/Study/AICompanion/src/aicompanion/bridge/community_knowledge.py`、`D:/Study/AICompanion/src/aicompanion/bridge/community.py`、`D:/Study/AICompanion/src/aicompanion/bridge/chat_card.py`，从单页首段摘录推进到多来源的词名、别名、释义、使用场合、作品 / 社区范围及来源版本。
- 公开 Wiki / 作品官方资料用于长期知识，热点单独短期缓存；已有世界书格式继续兼容。不能仅把更多热榜标题计作学到更多含义。
- 生活常识 / 稳定梗义、游戏版本资料与热点采用不同核对周期；不再把全部长期知识统一 30 天丢弃。冲突或无法核实的内容保留待核实状态，不替换可信旧条目。
- 群内黑话 / 表达偏好仅按已有授权会话范围学习，群特有含义按群存放；原聊天、个人标识和私人事实不作为联网检索输入。
- 资料批量更新在后台按额度和周期执行，回复只取少量相关知识。同一轮表达决策新增请求仍为 0，不能为了选图再问一次模型。

这部分与图片元数据检索分模块验收，避免把图库扩容当成知识学习已经完成。
