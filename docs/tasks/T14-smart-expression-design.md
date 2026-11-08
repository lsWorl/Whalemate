# T14 智能表情表达：实际实现与协议

日期：2026-11-08。实现方：Codex；状态：本地实现完成，待项目负责人 / Claude 验收，未推送。本文替代 T14-0 中与实际实现不同的假设；原始方案保留在 [历史设计](T14-smart-expression-original-2026-11-08.md)，冻结 v1 可由本分支提交 ca0a7ea 查看。

## 1. 基线与范围

- 开工重新 GitHub API + git fetch 核查 PR #10：37ae7d213a29bdb06ac8a8c0cfe9206b1e9fdadf，仍未合并。
- 在既有 task/t14-smart-expression / ca0a7ea 上继续，独立工作区为 C:/Users/27761/.codex/worktrees/t14-smart-expression/AICompanion；没有覆盖原 #10 分支。
- PR #11 T13（778a857）与 #12 架构约束（7d78765）只参考，均未合入。主窗口、schema / database 的本任务负责方已在 tasks 中登记为 Codex，待 Claude 复审。
- 保留原控制器、模型客户端、TTS / ASR、CueStream / task、角色包和现有调度；没有新增选图 LLM 请求、第二个聊天大脑或 Agent。没有实现 T15 好感度、T16 学习。
- 只对临时 / 合成库验证；没有读 .env、真实聊天数据库或账号配置，没有向真实 QQ 发测试消息，也未安装仓库新插件到运行中的用户实例。
- 五条约束继续适用：角色独立于模型；人格独立于记忆；跨渠道人物关联须授权；表达独立于 Agent 且保留习惯 / 关系；用户可自由塑造人格但不能突破安全边界。

负责人最新正式开发指令重新要求旧标签统一适配，因此本轮实现恢复了受约束的旧 <meme> 解析；它已取代冻结 v1 的“完全不兼容旧标签”决定。不存在两套随机发送逻辑。

## 2. 真实调用链

```text
桌面输入 / 快捷输入 / ASR
 → MainWindow.submit / _pump / _start
 → ChatWorker.run
 → ChatController.generate
 → ContextService.build（原 channel / through_id / 删除水位）
 → ExpressionService.prepare（本地候选）+ prompt
 → 当前请求内的虚构协议示例（不落库）+ 真实授权对话
 → 同一次 llm_client.generate_reply_stream
 → ExpressionStream.feed（先剥离图片控制）
 → 原字符串 token + ExpressionEvent
 → ChatWorker.expression_event（同一 Qt 队列）
 → MainWindow._expression_event → 原 _token / CueStream / SentenceBuffer / TTS
 → _display_attachment（前置）或 _result（后置）
 → receipt(displayed) → 主库使用 / 附件历史

原 LLM 主动问候 / 心事 / 分享
 → 原 _proactive / ProactiveMessageService / PurposeEngine
 → 同一个 _start / ChatWorker / generate，无新增触发或请求

QQ 私聊 / 允许群回复 / 有权限的群插话
 → 原 AstrBot / NapCat → POST /v1/chat
 → BridgeService.handle / _overhear（原鉴权、锁、额度和条数）
 → _reply（既有 SocialMemory / 聊天卡 / 社区语境）
 → 同一个 ChatController.generate / ExpressionService / ExpressionStream
 → ExpressionResult → 本机 JSON
 → 插件领取绑定 delivery_id → await event.send(有序消息链)
 → ACK accepted / failed / unknown → 原主库

固定本地台词
 → DesktopPetWindow.say_line → ExpressionService.local_result(text_only)
 → 原 show_bubble；不调用模型
```

系统错误、提醒、审批、工具进度仍由原通道输出。互动动画即时执行，不因为图片再发起聊天；reply_finished / work_finished 不递归调用 LLM。原群插话概率仍只控制“是否开口”，表情自身没有概率门槛或固定发送比例。

收图 / 头像仍由 QQMedia.describe / avatar_context 提供内部观察；原有视觉请求不变。回复继续看懂含义而不逐项分析表情画面。QQMedia 已移除 MemeOffer / offer / render 的发送责任，当前只保留收图、头像和字节校验。

## 3. 已实现模块

| 模块 | 实际职责 / 调用方 |
| --- | --- |
| expression/domain.py | 冻结数据类型；控制器、worker、桥接与服务直接使用 |
| expression/catalog.py | AssetCatalog：主库索引、真实图片校验、缩略图、导入 / 审核 / 启停 / revision；服务与图库页使用 |
| expression/service.py | 候选排序、prompt、唯一附件构造、结果、送达状态和历史；原控制器直接调用 |
| expression/stream.py | 增量图片标记剥离 / 旧标签统一映射；原控制器逐 token 调用 |
| ui/expression_image.py | 静图 / GIF / WebP 展示与资源释放；原聊天气泡 / 头顶气泡共用 |
| ui/library_page.py | 预览、详情、作者许可、标注审核、启停、后台导入 / 刷新、缓存清理；设置页挂载 |

没有为了未来建立空 AssetStore 或额外存储服务：AssetCatalog 就是当前可工作的存储适配器。未来 T13 接入只需调整其有效根目录适配，不重写候选 / 协议 / 平台层。

## 4. 当前数据协议

类型采用 frozen dataclass；程序生成 ID 和传输状态，模型只能回显候选 ID。类型不含可供模型指定的本机路径或下载 URL。

### ExpressionContext

generation_id、character_id、scope、channel、scene、trigger、proactive_reason、relationship、community、no_memes、more_memes、supports_images、early_before。

channel 仍控制原历史读取；scope 单独控制实际平台发送和使用记录。QQ 使用机器人账号 + 私聊 / 群范围，角色另有数据库字段隔离；缺少机器人编号时使用本次请求命名空间，不与其他会话共享表情历史。它不是跨平台人物标识。

### RelationshipSnapshot

identity_state=unknown、person_ref=None、relationship_kind=neutral、intimacy_allowed=False、link_state=unlinked。

当前所有入口默认未知、中性、无亲密权限、未关联。旧 trust / affection 不能提升这些权限；不根据头像、昵称或当前 QQ 绑定自动创建关系。保留现有主人人设称呼不等于授予亲密图片权限。T15 以后可提供明确授权的快照，T14 没有关系变化或评分。

### CommunityContext

community_key=None、entries=()。QQ 只复用现有选定社区的有界缓存语境，桌面首版允许为空；现有聊天卡、世界书和其启停仍保留。热点不是用户事实、人物关系或长期记忆。T14 不新增刷新 / 冲浪 / 长期学习任务，既有 QQ 调度保持原样。

SocialMemory 仍是角色 + channel + sender 下的有限偏好；no_memes 硬禁图，more_memes 只影响表达参考，不强制发图，不等同好感度，不把 QQ 偏好自动合并到桌面。

### ExpressionCandidate

asset_id、revision、summary、emotions、intents、image_text、category、tone_strength。最多 10 个，可为空。情绪、表达意图和语义标签分开存；模型 prompt 仅含 ID、含义、意图、情绪、图片文字和强度，不含原图 / 绝对路径 / 作者账号数据。

### ExpressionDecision

action=text_only / text_and_sticker / sticker_only；asset_id、placement 可为空；origin 为 model_marker 或 no_marker。最终 action 从干净正文和有效附件派生。模型不必返回 JSON；不记录隐藏思考。

### ExpressionAttachment

asset_id、revision、placement、delivery_id、media_type、content_sha256、alt_text。每轮至多 1 个，placement=before_text / after_text。delivery_id 是宿主生成的一次性发送标识，不能由模型指定。

领域附件没有路径或 base64。目录解析和编码是受信适配器的职责；QQ 的线载荷在附件上增加 data_uri，历史和模型都不保存该字段。

### ExpressionResult

turn_id、generation_id、character_id、delivery_scope、text、attachments、decision、status、diagnostics、version=1。status=complete / cancelled / failed / suppressed；text 已剥离图片控制和原 cue，不含传输信息。无附件是合法结果。

正常正文存在时，坏标记 / 无效 ID / 缺图回退纯文字。没有正文且没有可用图片则失败，不能留下永久“正在想”。群静默为 suppressed，整轮不发送。取消后的结果清空尚未投递附件；已经显示的前置图片另留真实送达记录。

### ExpressionEvent

kind=attachment_ready / final；generation_id；attachment 或 result。ChatWorker 在同一 Qt 事件通道中转文字与事件，不把两路 token 同时接到 UI。后台解析 / 校验，主线程只做展示；每次展示再次检查当前轮次、角色、启用状态与当前图库。

## 5. 资产模型、导入与存储

### 元数据 / 索引

expression_assets 记录 asset_id、revision、root、relative_path、sha256、metadata、thumbnail、review_status、enabled、imported_at。

metadata 包含角色、情绪、表达意图、语义、图片文字、强度、适用 / 禁用场景、对象、作者、来源、许可和原始标识；导入包还保留源版本、许可证据、条件与审核记录。文本 / 标签有类型和长度校验，强度 0～3。

- 内置图 ID 按原始内置标识稳定生成；本地素材按稳定来源标识或内容哈希建立 ID；完整 SHA256 单独校验。
- 相同文件复用 ID，不因重命名 / 换根目录重新生成；重复图片按哈希去重。相同上游标识更新内容保留 ID、更新 revision，并恢复待审。
- expression_asset_revisions 保存各图片版本的文件引用；已有旧文件仍存在时，历史能恢复旧 revision，不能拿新版冒充旧图。
- 未知许可、待审核、禁用、越界、缺失或被修改的图片不能成为可发送候选。Coopanion 路径、来源或导出标识拒绝。
- 图片支持 PNG / JPEG / GIF / WebP；单图 1 MiB、8192 边长、16M 单帧像素、240 帧与累计 32M 像素边界。所有动画帧解码校验。
- 缩略图选有代表性的中间帧并存入现有 SQLite；不创建 C 盘下载 / 缩略图目录。

### 路径依赖

只有既有 qq_meme_root 控制用户图库；未配置时使用仓库已有原创姿态。导入前需通过现有“聊天平台”选择非系统盘目录，不覆盖该设置。T13 尚未合入，本轮没有另造路径记录、盘符推荐、数据库搬迁或第二套根目录配置。

T13 后续继续负责 Settings.meme_directory、storage_paths_file、locations.plan_change / prepare / copy_database、backup / quick_check、下次启动生效。表情索引与缩略图在主库中，因此随主库备份；外部原图仍须由已确认的素材搬迁流程处理，不能声称 SQLite backup 自动复制图片目录。

### 导入 / 重建

1. 本地图像或本地素材清单，原始文件保留。
2. 格式、体积、解码、SHA / 来源 / 元数据校验。
3. 只在已选根下按内容哈希命名，临时文件写完 / fsync 后发布；目标相同则复用，不同则拒绝覆盖。
4. 索引 / 缩略图事务保存。原库审核 / 启停 / 使用记录不被刷新重置。
5. 素材清单最多 2048 项、1 MiB；相对文件必须留在清单目录，禁止越界，不接受 URL 当文件。
6. 索引刷新扫描本地文件，不联网重新下载全库；清缓存只清索引中的缩略图，不删原图。

图库页可预览、查看语义 / 作者 / 来源 / 许可详情、编辑意图 / 情绪标注、确认许可、启停、查看最近使用和缓存空间、导入图片 / 素材清单及刷新索引。耗时导入和历史图片加载复用 TaskWorker，退出等待原任务生命周期。

### 主库 v5 → v6

沿用 Database.initialize 的升级前 SQLite backup、事务与原记录保留，只增加：

| 表 | 内容 |
| --- | --- |
| expression_assets | 当前资产索引与缩略图 / 审核 |
| expression_asset_revisions | 旧图片版本的原文件引用 |
| expression_deliveries | 轮次、角色、scope / channel、资产 / revision、顺序、说明、发送状态和时间 |

不修改原角色、长期记忆、聊天卡或关系数值。重启时 pending 作废，sending 转 unknown，不能自动重发。现有已确认附件从同一 channel / through_id / 删除水位范围补入短语义，只作为“角色曾发表情”的上下文，不提取为用户事实。

## 6. 候选和表达决策

ExpressionService.prepare 先硬过滤，再本地检索：

- 角色匹配、已审核 / 启用 / 明确许可、真实文件可用。
- 未知关系不提供亲密图；公开场合仍限制亲密内容。明确严肃、低落、冲突、紧急话题首版直接纯文字。
- 检索同时使用当前上下文、最近原授权对话、主动原因、角色风格、现有社区语境、图片含义 / 意图。
- 小型同义词扩展使“做完项目”等语境可检索庆祝 / 认同 / 鼓励候选，但不直接发送、不强迫配图。
- 稳重 / 克制与活泼 / 夸张的风格会影响语气强度排序；最终仍是原模型选或不选。
- 最近同图短暂排除，同语义软惩罚；各真实会话隔离。pending / sending 只是预约抑制，不算已使用；displayed / accepted 才计历史，冷却后可复用。
- 意图多样性限制同类候选占位，每轮 0～10 张。没有候选时仍自然纯文字回复。

原 LLM 在同次生成中组织台词和选择 ID。没有分类情绪后直接发图的流水线，没有固定表情概率，没有工具调用循环或第二次“选图 / 修复”请求。

## 7. ExpressionStream 实际语法与行为

```text
<sticker asset_id="st_0123456789abcdef" position="before_text"/>
好耶，终于做完啦！

好耶，终于做完啦！
<sticker asset_id="st_0123456789abcdef" position="after_text"/>
```

仅标记就是纯表情；没有标记就是纯文字。建议首 / 末行，解析也接受可判定的行内形式。before 必须出现在正文前；after 之后出现新正文会使图片选择作废，正文仍保留。

实际 API：ExpressionStream(candidate_ids, legacy_map).feed(chunk) -> str，finish() -> str；selection 为可选的 (asset_id, placement)，diagnostics 为最多 16 个诊断码；cancel() 不可恢复。

- 逐字符状态处理跨 Token、引号、关闭符、CRLF，保留前缀与控制段上限 512。
- 已识别的坏控制段清理，超长段在关闭符或换行处恢复正文；未知普通角括号 / 数学表达式原样输出。
- Markdown 围栏、行内代码、引用行和普通 JSON 的字面内容不执行。字面示例不是机器控制；不引入表达 JSON 自动修复。
- 原 task 独立保护，不执行其中的图片声明；有效 task 保持原文交给 CueStream。动作词表、task 提议与工具审批流程不变。
- 第一条完整声明占唯一选择槽；无效 ID 不读文件，后续声明不能试探另一张。重复标签不导致重复图片。
- 控制在 controller yield 之前清理，因此不会进入正常 UI、TTS 或新历史正文；诊断只保存码，不存原始标签 / 路径。

前置图在后台校验后，attachment_ready 事件先于正文进入同一 UI 队列；展示成功再处理后续文本。没有原设计假定的 1 秒独立等待器：当前后台直接完成本轮有限图片校验，不阻塞 GUI，也没有额外的正文缓冲队列。后置图在完成 / CueStream flush 后展示。图片已显示后不能假称撤回，取消只阻止尚未发生的发送。

reply_filter 的原签名保留，仍在最终历史写回前执行；只要有最终过滤，early_before 强制关闭。QQ 因此完整过滤后再发图，【不说】即使伴随合法图片也保持整轮静默。

## 8. 旧协议统一适配

<meme name="分类"/> 由同一个 ExpressionStream 接收，经 ExpressionService.legacy_map 映射到本轮真实候选中的一个稳定 ID。它不额外扫描图库，不随机取图，不绕过关系 / 许可 / 冷却。

新旧标签混合仍只有一个选择槽；旧 QQMedia.render 已不存在，插件也不从旧 images 字段自动补图，因此不会双发。

旧分类目录和 manifest + memes/<分类> 保留；明确作者 / 来源 / 许可可索引启用，缺少这些信息先待审并在图库页确认，不把未知许可偷偷当已授权。旧配置键不删除，旧历史分类说明保留，不伪造某张具体图片已送达。

## 9. 桌面与 QQ 适配

### 桌面

复用 ChatView.Bubble / MessageHandle、SpeechBubble / BubbleStack；ExpressionImage 从已校验字节显示 QPixmap 或 QMovie，限制尺寸、按 Qt 对象生命周期释放设备和动画。纯图不保留“正在想”、不合成空语音；坏图有文字则保留文字，纯图失败显示本地失败状态。

ChatWorker 后台读取 / 校验图片，文字和事件统一发到主线程。每个事件校验 generation_id；角色切换、打断、退出后丢弃旧事件。不可见的两个桌面表面不假记 displayed。

历史按已确认附件恢复，图片丢失 / 旧文件被替换就降级，不能替换成别的随机图。_sync_delivery 只更新文字，不抹掉已显示图片；原摘要 / 私密范围没有扩张。

### QQ 线协议与发送

新插件请求 expression_version=1、bot_account_id；服务端返回原 reply / silent / reason，加 expression_version 和 expression（Result 视图，附件增加 data_uri）。未知旧客户端只收纯文字信封，不获得未确认的图片；新客户端仅消费一份表达结果。

- before_text → [Image, Plain]；after_text → [Plain, Image]；纯图 → [Image]。
- 插件校验体积、base64、SHA256、位置和 delivery_id 后领取发送资格。
- /v1/expression/claim 只接 delivery_id；服务端绑定原收件会话、角色、当前群权限、图库开关、审核 / 文件状态和 120 秒时限，不能改收件人或文件。只领取一次。
- 等 await event.send(event.chain_result(chain)) 成功后 ACK accepted；明确 ActionFailed 记 failed，超时 / 连接不确定记 unknown，不自动重发。
- /v1/expression/ack 只接 delivery_id / status，可幂等补报；unknown 可被可信的迟到 ACK 确认，但不能重新领取发送。
- 平台接受不是对方已读。主脑锁不会一直占到平台网络返回；服务重启 / 关闭、角色切换或群权限撤销使旧待发结果失效。
- 图片失效或开关关闭可纯文字降级；权限撤销则整条旧表达不发。原收图、头像、额度、静默、旁听和离线不换人格保留。

## 10. 素材与参考

另有 D:/Study/AICompanion/output/t14-sticker-pack/manifest.json，可通过图库“导入素材清单”选入已有非系统盘图库。默认仍保留 6 张仓库原创图，不自动改用户图库。

可导入包含 80 张去重 GIF，来自原作者公开 GIF 路径，逐项保留作者、来源、许可条件、原始标识、源版本、审核记录与完整 SHA；首帧 / 中间帧目检及完整帧解码通过。抽帧不是逐秒人工观看保证，实际聊天观感仍待用户验收。排除的 3 张及额外缓存不会进入正式导入清单。

版权条件需保留：作者“可以叫我云大人 / 雾雪”的主页与 kiriyuki.com；GIF 按公开非商业署名分享条件整理，不能用代码 Apache / MIT 许可覆盖图片，也不能改称 GIF 为 CC BY-NC；知识标注另为 CC BY-NC 4.0。未知授权继续待审。没有使用 Coopanion 代码、贴图或导出文件。

一手参考：

- [大肥鱼素材与知识库](https://github.com/Loping151/dsh-nekowhale-stickers/tree/b7b58c0ff347d639fabe87d90d6716c4b3cad185)：复用其有来源标注和素材信息，不运行其 Agent / MCP / 随机发送代码。
- [LICENSE-ASSETS](https://github.com/Loping151/dsh-nekowhale-stickers/blob/b7b58c0ff347d639fabe87d90d6716c4b3cad185/LICENSE-ASSETS)：分别记录知识数据与图片条件；包内 SOURCE-LICENSE.txt / asset-review.json 可审计。
- [AstrBot 表情包管理器开发说明](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/4d2f870e34b54b2c13214e33f9497b71b113344e/DEVELOPMENT.md)：参考逐轮解析、候选复验、取消与成功记录的边界；没有复制代码或安装第二套自动发图插件。
- [档案站图片权利说明](https://github.com/lmy414/bluedafeiyu/blob/main/README.md)：代码许可不替代图片授权；本轮未批量导入该站素材。
- [AstrBot 4.29.3 OneBot 发送实现](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.3/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py)：已用实际安装的该版本验证公开 send API，底层换成合成客户端，没有真实 QQ 发送。

本轮未增加运行依赖；Pillow 仅用于已有环境中的审图联系表，不成为产品运行依赖。

## 11. 测试与实施记录

| 阶段 | 实际实现 | 对应验证 |
| --- | --- | --- |
| T14-1 | 主库资产 / revision、原子导入、审核隔离、缩略图 / 素材清单 | test_expression_catalog；真实 v5 固定 SQL 夹具、失败注入与重复导入 |
| T14-2 | 中性上下文、场景 / 对象过滤、人格强度、上下文检索和多样性补充、重复抑制 | test_expression_service；不新增模型请求 |
| T14-3 | 增量语法、旧标签同槽、原 controller 单次生成接入 | test_expression_stream / controller；逐切分、坏标记、普通 JSON / 代码、task、取消 / 新旧 generation |
| T14-4 | 原桌面静图 / GIF / 历史 / TTS、纯图与失败降级 | test_expression_ui + pet_smoke_check 的真实表达与历史恢复 |
| T14-5 | 原桥接 / 插件顺序、领取、ACK、超时未知 / 幂等 | test_expression_qq、原媒体 / 插件 / bridge 回归；实际 AstrBot API + 合成 OneBot |
| T14-6 | 图库管理、80 张非商业素材包、全回归和报告 | 全部离线测试、Windows Smoke、D 盘 80 + 80 重复导入 / 10 候选实测 |

最终专项 61 项通过（4.662 秒）；tools/run_checks.bat 共 448 项，441 通过 / 7 跳过（33.534 秒），Smoke passed=true、remaining_workers=0，统一表达 / 单次生成 / 正文清理 / 图库 / 历史检查全部通过。跳过项为 Windows 下的 POSIX 进程组测试 1 项、未安装可选 Playwright 的渲染测试 3 项、独立工作区不提供 Coopanion 的本地检查 3 项。

中间一次 Smoke 发生原生退出 0xC0000005，无 Python 栈；PR #10 历史已有相同现象，不能据此断言原因相同或排除本次影响。故障记录已保留，启用 faulthandler 后的针对性复测及最终全量复测通过，但根因仍未关闭。本轮同时修复 run_checks.bat 吞掉失败退出码的问题：单测或 Smoke 任一失败均返回 1，三个合成退出码组合已验证。

测试结果、耗时、跳过项、最终 HEAD 和文件清单以 D:/Study/AICompanion/output/T14-development-report-2026-11-08.md 为准。所有新增单测使用合成数据，素材验证使用 D 盘全新临时库。没有以模拟返回值冒充平台真实收发；未执行的真实模型 / QQ / 用户库迁移均明确列出。

## 12. 与冻结 v1 的实际差异和后续边界

1. 最新正式任务重新要求旧标签统一兼容，已实现唯一解析 / 发送责任，取消“直接删除兼容”的旧决定。
2. 用实际 feed -> str / selection 接口接原 generate 字符串流，事件仅用于附件 / 最终结果；避免无调用的复杂类型层。
3. AssetCatalog 直接适配原 SQLite；缩略图入库，不依赖未合入 T13 的新文件缓存路径，不重造目录管理。
4. 保留原本就有的 LLM 主动发言能力；固定本地行为使用统一 text_only，不因表情新增调用。
5. 取消复杂表达 JSON 修复，只保护普通 JSON / 引用；实际协议只有 sticker 控制行。
6. 稳定 revision 增加历史文件引用表；未知平台结果不能自动重发，真实确认可迟到补报。
7. 新素材以独立 D 盘可导入包交付，未覆盖或自动迁移用户现有图库。

尚未真机验收：真实 DeepSeek 生成自然度、QQ 私聊 / 群平台显示（含 GIF 表情气泡样式）、用户库正式升级 / T13 路径迁移、长期运行和打包版。首次实施阶段由用户在真机看过才算验收，自动化通过不替代它。

T15 接入点为 ExpressionContext.relationship 的授权快照；不允许从 legacy trust 推断。T16 接入点为 CommunityContext 的有来源、有限且按范围读取的语境；不得把热榜变成人物事实。后续模型 / Agent 更换仍复用同一资产 / 使用历史与角色身份，不清空共同经历。

没有推送、创建或合并远程 PR。完成本轮本地交付后停止，等待负责人验收。


## 13. 2026-11-08 验收反馈修复：上下文决定表达，不按词表触发

本节覆盖前文旧版有关关键词 / 语气词检索扩展和历史附件注入的描述，以实际代码为准。用户反馈：多数普通聊天不配图，且出现 `[角色已发送表情：…]` 占位文字。用户本轮单独授权仅读测试库最近 30 条及对应发送 / 图库状态；未读长期记忆、未改库，原始对话不进入报告。核查确认占位回合没有实际图片发送记录，80 项资产审核 / 启用正常。

### 13.1 历史与展示数据分离

旧版 ContextService 把附件回执拼成 assistant 正文，模型会照抄。现在确认的附件仅以有界 JSON 数据加入只读系统背景（最近 6 条、原渠道 / through_id / 删除水位不变）；不伪造成她说过的话。发给模型的 assistant 历史副本清理旧内部注释，不改真实数据库。ExpressionStream 跨 token 拦截精确内部注释前缀，不把任何方括号词自动转为图片；普通方括号、代码、引用、JSON 保持字面内容。旧数据库中的占位记录没有被修复成虚构图片，也不会重新发送。

### 13.2 候选检索不承担表达决策

已移除“收尾 / 摸鱼 / 哈哈”等同义词触发扩展、按用户文本正则硬判严肃情境及按人格词切换语气强度。正则只用于分词、协议语法和资产亲密标注约束，不用于把用户话语判为某种情绪后决定发图。已知 serious / conflict / emergency / grief 场景值仍可整体禁图，不能靠文本包含“崩溃”等词设置。真正悲伤、玩笑、严肃事务由同一聊天模型阅读完整语境判断。

本地只做合格素材召回：读取上游 semantic_tags、语义 / 文字 / 情绪 / 意图，以本轮上下文为主，限制长人设与旧话题的词重合权重；前 6 个相关候选与最多 4 个表达多样性补充，共不超过 10 张。词重合影响候选排序，不直接生成 ExpressionDecision，更不会自动发送。关系、审核、许可、文件、去重和 QQ 冷却约束保留；大脑仍可不选任何图。

### 13.3 同轮表达格式与容错

增加可选纯文字选择标记 `<expression mode="text_only"/>`，与 sticker 占同一个选择槽，解析后不进入正文 / 历史 / TTS。模型漏写选择标记仍容错接受正常文字，不以格式错误丢失回复；不能先选 text_only 再偷偷发图。普通 JSON / Markdown 中的字面协议仍不执行，畸形 expression 与 sticker 一样有界清理。

ExpressionService.format_examples 提供一对图像与一对纯文字的明确虚构协议示例；ChatController 只在当前 LLM 请求中放在真实对话之前。候选召回只看真实历史，示例不落库、不参与记忆提取、不作为共同经历、也不增加 LLM 请求。这解决了模型虽看见文字规则却不采用实际图片语法的问题。提示区分图片协议与人设里禁止的 emoji / 情绪标签 / 动作旁白，保留用户纯文字要求，不设置发图概率或比例。

### 13.4 实际验证和限制

- 表情专项 76 项通过，5.464 秒；最终 tools/run_checks.bat 463 项：456 通过 / 7 跳过，34.737 秒，Smoke passed=true / remaining_workers=0。原跳过原因不变。
- 用户本轮另行授权仅读 .env 的模型 API 字段，使用仓库角色 / 公开素材 / 全新临时库和合成对话真实调用配置的 DeepSeek。本轮累计 43 次请求（含失败设计的对照迭代），每轮自然回复只有 1 次请求。无用户聊天上传、无真实 QQ、无残留测试脚本；临时库已自动清理。
- 必须保留失败结果：早期仅改提示 / 词表方案没有改善真实模型自主选图，后来按用户要求移除词表规则，采用同轮格式示例后才出现自然选图。早期数据只作过程记录，不算最终验收成功。
- 最终同末句“绷不住了”：搞笑短片上下文选择坏笑 / 恶作剧图；家人住院上下文即使有 10 个候选仍选择纯文字。项目收尾分享主动选认可 / 盖章图，没有要求发表情。主动问候样例仍选纯文字，允许如此，但尚未实证这组样例中的主动发图自然度。
- 没有把生成附件视作平台送达；真实验证只核验所选 ID、语义和图片字节，桌面显示由 Smoke 验证，QQ 仍是原模拟 / 实际 API 层验证。
- 仍有模型无依据口吻、自然度、主动表达覆盖和多轮稳定性需真机复验。先前间歇 Qt 原生崩溃的根因仍未关闭，不因本轮 Smoke 通过而宣称修复。

本轮补充报告：D:/Study/AICompanion/output/T14-expression-acceptance-fix-2026-11-08.md。没有推送、合并或新增 PR 评论；原 T14 交付是修复前历史基线，不覆盖当时的证据。

## 14. 2026-11-08 Claude 接手：她为什么不爱发表情，以及怎么改

本节以实际代码为准，取代第 13.3 节的"每轮先写 text_only 标记 + 虚构示例对话"做法；其余架构（一次生成、ExpressionService / Stream / Result、桌面与 QQ 共用、claim / ACK）不变。

### 14.1 根因（沿真实调用链）

| 环节 | 原实现 | 后果 |
|---|---|---|
| 历史 | `ContextService` 把她发过的图只放进系统提示末尾的 JSON 回执；纯图回复的 assistant 行正文为空，直接从历史里消失 | 模型看到的"自己"从来不发图。聊天模型强烈模仿自己前文的说话方式，越聊越只发文字，形成自我强化 |
| 协议 | 要求**每轮先写** `<expression mode="text_only"/>` 或 sticker，并在真实对话前塞两轮"虚构示例"，其中一轮示范"不用图，我在，慢慢说" | 第一个 token 就要先做选择；"纯文字"是更省事、被示范过的那条路 |
| 示例 | 虚构示例固定用排名第一的候选 | 用图时倾向照抄那一张，图文不符 |
| 编号 | 候选只给 32 位十六进制 `st_…` ID，必须原样抄回 | 抄错一位就 `invalid_asset`，整张图作废 |
| 解析 | 属性顺序必须固定；图在后之后再有任何字（包括 `<挥手>` 这类动作标注）就作废；正文后写 before_text 作废；第一个标记无效后续也不再接受 | 模型"写了图但没发出去"，用户只看到文字 |
| 词义冲突 | 角色动画说明叫"【表情与动作】…用全角方括号标注表情"；人设又写"不输出 emoji、旁白" | 模型以为写了【开心】就算表达了表情，或者以为不许发图 |
| 亲昵图 | 桌面和 QQ 绑定号私聊都用默认"未知关系"快照 | 撒娇、贴贴、揉脸等 9 张（80 张包里的 owner_family）在桌面永远不会成为候选 |
| 聊天卡 | QQ 聊天卡的示例全是"对方发表情包 → 她只回一句话" | 群里学成"别人发图我回字" |
| 观测 | 没有单轮记录 | 无法区分"没候选 / 模型没选 / 格式坏 / 校验失败 / 没显示" |

另外两个无法从代码直接确认、但很可能影响真机的因素：用户实际图库如果是 PR #10 时期的文件夹，没有许可元数据的图全部 `pending_review`，候选为空；80 张素材包没有自动导入。新的观测记录会直接显示这两种情况（`no_eligible_assets` + `pending_review: N`）。

### 14.2 改动

- **协议改为本轮短编号**：候选按 `s1…s10` 编号，模型写 `<sticker id="s3"/>`。位置由标记出现的地方决定（前面没有可见正文 → 图在前，否则图在后），旧的 `position` 写法仍接受。属性顺序、引号、结尾 `/`、`asset_id=` 都宽松；无效标记不占名额；正文中间的标记按"图在后"发而不是作废；【开心】/<挥手> 不算可见正文。旧 `text_only` 标记仍隐藏，但不再阻止后面真的选图。完整 asset_id 不再出现在提示里（解析器仍接受）。
- **历史里写回她发过的图**：发给模型的历史副本中，她当时真正显示 / 被平台接受的那张图按原位置写成 `<sticker sent="含义"/>`（数据库原文不变，未送达的不写）。模型照抄这种写法时，含义和本轮某个候选完全一致才算选这张，否则静默隐藏。去掉系统提示里的 JSON 回执。
- **提示词**：把表情包写成"她说话方式的一部分"——能表达她自己的情绪和态度（得意、心虚、委屈、撒娇、被逗笑、困了……），可以配话也可以单独一张图当回复；闲聊、接梗、被夸、被吐槽、好坏消息、道别时有贴切的就大方发，不用等对方要，也别因为"文字也能说清楚"就不发；技术、事实、知识解释、严重的事用文字。明确【开心】标注和"不输出 emoji"都不是禁止发表情包。主动开场时说明可以用图打招呼或只发图。不设比例、不强制。删除两轮虚构示例对话和"每轮必须先选"的要求。
- **候选**：每条给"含义｜情绪｜用来（用法）｜图上文字｜语气冲"。5 张相关 + 按情绪 / 用法补齐多样性（她想表达的情绪不一定和对方的话字面相关）；刚发过的两张 5 分钟内不再给。
- **对象**：桌面使用者和 QQ 绑定号的私聊用 `owner_relationship()`（沿用现有"爹"绑定，不是 T15 评分），可以用亲昵 / 撒娇图；陌生人、群聊（包括爹在群里）不给。
- **聊天卡**：提示里注明示例只演示文字语气，想回表情包照【表情包】说明发；卡片内容本身不改。
- **主动开场**："只发一句" → "说一两句自然的开场或追问"。

### 14.3 单轮表达决策观测

`ExpressionService.record_trace` 每轮在 `ChatController.generate` 收尾时写一条摘要到设置 `expression_traces`（环形，最多 60 条）；界面显示 / 平台回执到达时由 `receipt()` 把阶段推进到最终结果。字段：是否启用、关闭原因、图库总数、可用数、各过滤原因计数、候选数、是否注入、模型写了几个标记、选中的 asset_id 与含义、位置、是否解析 / 校验通过、是否生成 ExpressionResult 并交给接收方、发送 / 显示状态、诊断码、阶段。**不记**聊天文字、图片字节、本地路径、对方身份。

| 阶段 | 含义 |
|---|---|
| A_no_candidates | 没有合适候选，`reason` 说明（client_no_image_support / memes_disabled / serious_scene / qq_cooldown / no_eligible_assets…），`filtered` 给出各过滤原因计数 |
| B_model_chose_text | 有候选、已注入，模型这轮选择纯文字 |
| C_marker_unparsed | 模型写了标记，但格式解析失败 |
| D_asset_invalid | 解析成功，但编号 / 图片文件校验失败 |
| E_not_delivered / E_pending | 结果被取消 / 过滤 / 没有接收方；或已生成等待显示 / 回执 |
| F_display_failed / F_send_unknown | 桌面显示或 QQ 发送失败；QQ 超时结果未知（不重发） |
| OK_displayed / OK_accepted | 桌面已显示 / 平台已接受 |

查看：设置 →"表情图库"页底部"最近的表达决策"；勾选"开发模式"或设环境变量 `AICOMPANION_EXPRESSION_DEBUG=1` 时，每轮一行摘要写到控制台（stderr）。

### 14.4 验收工具

`tools/expression_eval.py` + `tools/expression_scenarios.json`：34 个合成场景（情绪 10、社交 9、她主动 8、不适合发图 5、QQ 群 2），每个场景从同一个已导入图库的模板库复制独立临时库，走原 `ChatController` 一次生成，输出 JSON 和带"人工评估"空列的 Markdown 表。真实模型由用户自己运行（会产生少量词元费用）；`--offline` 只验证流程。专项指标"适合场景发图率 ≥ 60%"只用于检查是否过度保守，合不合适以人工评估为准，不是运行时比例。

### 14.5 第一轮真实模型验收（2026-11-08 21:17，`feb8e6b`，deepseek-flash，80 张素材包）

- Windows：表情专项 98 项通过；全量 485 项通过（跳过 7）；桌宠 Smoke `passed: true`、`remaining_workers: 0`。
- 34 场景：适合发表情 6/25（24%），两可 0/3，应纯文字 0/6；无出错、无重复图；阶段全部是 `OK_displayed` 或 `B_model_chose_text`，**没有 C / D 类失败**——格式和校验已不是瓶颈，剩下的是模型选择偏保守。证据：`docs/tasks/evidence/T14-expression-eval-2026-11-08-round1.json`。
- 观察：
  - 她把情绪写成不在动画词表里的方括号（【委屈】【心虚】【哼】【愤愤】【无语】【叹气】），这些既不会触发动画，也会原样显示在正文里——情绪表达被这个通道"吃掉"了。
  - 人设里"一般一两句""不输出 emoji"让回复偏短、偏克制；负责人要求更像真人，可以多说几句、可以用 emoji。
  - 主动场景 4/8 发图，用户发起的情绪 / 社交场景明显更少。
  - "晚安"选了"画稿画到闭眼"——80 张包里没有真正的晚安 / 睡觉图，属于素材缺口。
- 本轮调整（`feb8e6b` 之后）：
  - 人设 v4：允许适度 emoji；回复长短随内容，可以连着说几句（换行分开），不是一问一答。v2 / v3 原文都能升级，用户改过的不动。
  - 系统提示、QQ 私聊 / 群回复、主动开场、心事提起：去掉"一两句"限制，改成长短随内容。
  - 表情提示：情绪明显时通常就是该配图的时候；想写动画列表里没有的情绪时别写方括号，去候选里找图；写回复前先看一眼候选。
  - 验收脚本记录每个场景模型看到的候选，区分"没有合适的图"和"有但没选"。

### 14.6 第二轮真实模型验收（2026-11-08 21:28，`e0f19dc`）与候选召回重做

- Windows：表情专项 98 项通过；全量 488 项通过（跳过 7）；Smoke 通过。
- 34 场景：适合发图 8/25（32%），应纯文字 0/6；1 次模型空回复（上游 `LLMResponseError`），1 次 `invalid_asset`。回复明显变长、会连说几句、偶尔用 emoji。证据：`docs/tasks/evidence/T14-expression-eval-2026-11-08-round2.json`。
- **新发现的主因：候选每轮几乎是同一批。** 验收脚本记录的候选显示，34 个场景里有 20 多个看到的是同样十张（"看到迷惑发言""遇到特别开心的事""被人嫌弃只吃饭"……）。原因是词面相关度对中文短句几乎总是 0，同分时按 asset_id 排序，多样性补齐也是确定性的。于是"都凌晨一点了你困不困"时手边没有困的图，"喝完你的奶茶"时没有生气的图，被夸时没有得意的图——她选纯文字其实是合理的。
- 修复（`expression/service.py`）：
  - 候选按十种常见情绪铺开（开心、得意、委屈、无语、疑惑、困、撒娇、生气、调侃、打招呼），每组至少一张，她想表达自己的情绪时手边有对应的图；剩余名额仍按"未覆盖的情绪 / 用法"补齐。
  - 对话里的情绪线索（"困""睡""凌晨"、"冤枉""是你"、"夸""靠谱"……）把对应情绪组的图排到前面。**只影响候选排序，不决定发不发**，没有"出现某词就发图"的规则。
  - 同一情绪组内按本轮 generation_id 打散，不同轮次看到的是同一情绪的不同图，避免总是那几张。这是"给她看哪一张"的轮换，不是发送概率。
- 其他：
  - 传入角色动画词表时，词表外的短情绪方括号（【委屈】【无语】……）会被隐藏，记 `stray_emotion_tag` 诊断。这些标注本来就不触发动画，只会露在正文里。词表内的标注、【不说】、长文字、代码里的内容都保留。
  - 图在前时正文开头的空行去掉；正文中连续 3 个以上的换行合并成一个空行。
  - 观测记录里增加 `rejected`：模型写的无效编号（只记形如 `s12` 的短值），便于分析 D 类失败。

### 14.7 第三轮真实模型验收（2026-11-08 21:39，`1feb59e`）

- Windows：表情专项 105 项通过；全量 495 项通过（跳过 7）；Smoke 通过。
- 34 场景：适合发图 10/25（40%），应纯文字 0/6，两可 0/3；用到 9 张不同的图；没有出错，没有 C / D 类失败。证据：`docs/tasks/evidence/T14-expression-eval-2026-11-08-round3.json`。
- 候选已按情绪铺开，各场景看到的图不同了。在 15 个"该发没发"的场景里，大多数手边其实有对得上的图（被冤枉时有"委屈得想缩起来"，困时有"困到闭眼"，被夸时有"打满分盖章"，疑惑时有"满头问号"）。**瓶颈已从候选转到模型的选择习惯。**
- 本轮调整：在提示最末尾加一句收尾习惯——"一条消息写完，回头看看它带着什么情绪；情绪明显、上面又有对得上的图，就在最后单独一行配上；聊正事或没有对得上的图就不配"。末尾放这句，是利用模型写完正文后的那一刻做选择，而不是像旧协议那样要求开头先做决定（开头先决定会偏向纯文字）。不设比例，不强制。
- 说明：验收脚本里的场景大多只有一轮，没有"她以前发过图"的历史；真实长聊天里，她发过的图会写回历史（14.2），模型会跟着自己的习惯走，实际使用率预计比单轮验收更高。这一点还需要真机验证。

### 14.8 第四轮真实模型验收（2026-11-08 21:44，`76f3c9a`）

- Windows：表情专项 105 项、全量 495 项通过（跳过 7）；Smoke 通过。
- 34 场景：适合发图 13/25（52%），两可 2/3，应纯文字 0/6；用到 14 张不同的图，重复 1 次；没有出错，没有 C / D 类失败。四轮趋势：24% → 32% → 40% → 52%。证据：`docs/tasks/evidence/T14-expression-eval-2026-11-08-round4.json`。
- 图文匹配初步检查：被冤枉配"委屈得想缩起来"，困配"困到闭眼"，主动撒娇配"撒娇求关注"，好消息配"开心炫耀"，都合适；"被捏脸委屈求放手"配"奶茶被喝光"、"被逗弄小惩罚"配"咖啡泡面"偏弱——素材里没有更贴切的"心疼 / 震惊"。人工评估尚未做。
- 发现：图在后时，正文结尾会留一串空行。已修复：结尾空白先压着，后面还有字才放出去。收尾习惯的情绪列表补上开心、激动、无语。
- 单轮单次跑波动较大，下一轮改为每个场景跑两遍（`--rounds 2`）看稳定值。

### 14.9 第五轮（每场景两遍，2026-11-08 21:52，`c1dd9e4`）

- Windows：表情专项 105 项、全量 495 项通过（跳过 7）；Smoke 通过。
- 68 次：适合发图 20/50（40%），两可 1/6，应纯文字 0/12；17 张不同的图；没有出错，没有 C / D 类失败。证据：`docs/tasks/evidence/T14-expression-eval-2026-11-08-round5.json`。
- 两遍取样后的稳定值是 40% 左右，第四轮的 52% 是单次波动。拆开看：
  - "她主动"的场景（含"她说错话"）12/16（75%）；
  - 回复用户的情绪 / 社交场景只有 8/34（24%）。
- 差别来自提示：主动开场里明确写了"可以先发一张图再说"，回复用户时没有对应说法。
- 本轮调整：回复用户时也给出同样的表达方式——"回别人消息时，你常常先用一张表情包表达第一反应（被逗笑、被冤枉、被夸、惊了、无语、心疼、馋了……），写在最前面，再接着说话；第一反应平平淡淡、或者对方在说正事，就直接说"。这和旧协议"每轮开头必须先声明纯文字或选图"不同：没有标记义务，不选图时什么都不用写。
- 验收工具新增"多轮对话"场景（`chain`）：两段各 5 轮的连续聊天在同一个库里进行，她前面发过的图会写回历史，用来观察多轮下的表达习惯。单轮场景测不到这一点。

### 14.10 第六轮（2026-11-08 22:00，`600c2a6`，每场景两遍 + 两段 5 轮连续对话，共 88 次）

- Windows：表情专项 106 项、全量 496 项通过（跳过 7）；Smoke 通过。
- 发图率：适合发图 40/64（62.5%），两可 4/10，应纯文字 0/14；回复用户 24/34（71%），她主动 12/16（75%），连续对话 4/14（29%）；27 张不同的图。证据：`docs/tasks/evidence/T14-expression-eval-2026-11-08-round6.json`。
- Claude 初步人工评估（`…-round6-review.md`，待负责人复核）：发了图的 44 次中合适 33、勉强 7、不合适 4；**适合场景里"发了且合适"为 30/64（47%），算上勉强为 36/64（56%）**。发图频率已经不保守，但"合适"还没到 60%。
- 不合适 / 勉强的主要原因是**素材缺口**：80 张包里没有"恭喜 / 心疼 / 震惊 / 困惑 / 晚安"这类通用反应图，模型拿"掏出秘密武器""有话不好直说"凑数。这需要补素材（T5 / 负责人提供有许可的图），不是再加提示能解决的。
- 连续对话发图偏少：前几轮选了纯文字后，历史里的她一直只发字，后面也跟着不发。真实长聊天里这种惯性两个方向都有，需要真机观察。
- 3 次 `invalid_asset`：观测当时只记 ASCII 编号，没记下模型写了什么；推测是照抄了说明里的占位词"编号"。已修：说明改为 `<sticker id="s3"/>` 这样的具体写法；模型把含义当编号写、且只对得上一张候选时按那张发（记 `meaning_as_id`）；`rejected` 记录 12 字以内的原值。
- 已知小问题：她偶尔把动作写成 ASCII 方括号（如 `[歪头]`），会原样显示；未处理。
