# T14 后续实现交接：Claude Code 开发，Codex 审计

日期：2026-11-08。依据项目负责人最新指令：将 T14 当前代码提交 PR，后续实现移交 Claude Code，Codex 负责审计。本文件是接手清单，不代表 Claude 已经开始运行或 T14 已经验收。

## 1. 从哪里继续

- 仓库：lsWorl/AICompanion。
- 开发分支：task/t14-smart-expression。当前运行代码基线为 `4af066f29937403fe70f73b36dca68f5b7d60a6b`；本次另有纯文档交接提交，获取远程最新 HEAD 后继续，不重置已有修改。
- 堆叠基线为 PR #10 / task/t12-qq-vision-memes / `37ae7d213a29bdb06ac8a8c0cfe9206b1e9fdadf`。2026-11-08 创建 PR 前再次核对，该 head 未变且尚未合并。
- PR #11 T13 为 `778a8576630a235b08bc249afa578f04ac2ce3c8`，#12 为 `7d7876585bf476f2667686396d231eca6d98c94b`，都未合并，只参考。不要擅自合入、重设 PR 基线或在 #10 原分支上编码。
- 继续使用本任务分支 / PR，确保同一时段只有 Claude 写代码。Codex 暂停 T14 实现，只做读取、验证及审计。若 Claude 本地已有修改，先保留并核对，不覆盖、不 force push。
- 本任务共享文件 ui/main_window.py、storage/database.py / schema.py 的后续修改由 Claude 单方负责，Codex 审计；config.py / pyproject.toml 如确需改动先在任务记录说明范围，不引入第二套目录管理或并行 Schema 修改。
- 同机独立工作区：C:/Users/27761/.codex/worktrees/t14-smart-expression/AICompanion。D:/Study/AICompanion 的运行检出仍是 PR #10，不能将旧实例的表现误记为 T14。

## 2. 已实现并存在实际调用链的功能

- 主库 schema 6：expression_assets、expression_asset_revisions、expression_deliveries；资产稳定 ID、SHA / revision、审核隔离、原子导入、去重、缩略图 BLOB、历史附件。
- ExpressionService 在原 ChatController 中召回最多 10 候选，许可 / 审核 / 角色 / 关系 / 场景 / 最近使用先过滤；同一次原 LLM 请求决定文字及 0～1 张图片。
- ExpressionStream 在正文交给 CueStream / TTS 前清理控制语法；支持前 / 后图文、纯图、取消、错误 ID、JSON / 代码保护。旧 meme 标签只走一个兼容槽，旧随机发送已移除。
- 原聊天气泡 / 桌宠气泡支持静图、GIF、纯图和历史恢复；设置“表情图库”支持预览 / 审核 / 标注 / 启停 / 扫描 / 导入。
- QQ 仍是 AstrBot + NapCat 接同一大脑，插件源码 0.6.0；claim / ACK / delivery_id 幂等、超时 unknown 不重发、1 MiB 限制、发送前再次检查权限。
- 接收图片理解、原角色 / ASR / TTS / CueStream / task 和主动调度沿用原链路；没有选图专用 LLM 或第二个 Agent。

请先读：

1. docs/tasks/T14-smart-expression-design.md，特别是第 13 节最新修复；前期方案与最新冲突以第 13 节为准。
2. src/aicompanion/expression/{domain,catalog,service,stream}.py。
3. controllers/chat_controller.py、services/context_service.py、ui/chat_worker.py / main_window.py。
4. bridge/service.py / server.py 与 integrations/astrbot_plugin_aicompanion/main.py。
5. tests/test_expression_*.py；docs/tasks/evidence/T14-expression-validation-2026-11-08.json。

## 3. 已确认的问题和本次修复

用户实测反馈：很多日常消息不配图，必须叫她发表情；有时只显示 `[角色已发送表情：…]`。

已定位并修复：旧 ContextService 把实际附件说明拼成 assistant 台词，模型照抄后并没有真正发送图片。现改为只读背景数据，历史副本清理内部说明，流式拦截精确内部注释；不重写旧库、不把旧文字伪造成图片、不自动补发。普通方括号 / JSON / 代码不是表情触发器。

负责人最新明确要求：**根据完整上下文与她自己的表达意图选择，不得用正则匹配用户提示词决定发表情。** 现已移除话题词表扩展、用户文本严重性正则开关和人格关键词语气开关。本地文字相关性只用于候选排序，不直接产生发送决策；不要为了通过样例把“哈哈”“收尾”等写回触发规则。

模型仅靠说明仍经常不采用图片语法。现同一请求加入明确标为虚构的协议示例，不落库、不当共同经历；`<expression mode="text_only"/>` 表示显式选择纯文字，与 sticker 共用单槽。漏标记仍接受正常文字，不强迫图像比例。此机制需要继续复核自然度与例子是否会影响模型措辞。

## 4. 接下来按顺序做什么

### A. 先复现和审查现有修复

- 从远程最新 HEAD 建立隔离开发环境，先跑离线全量及 Smoke。记录源码路径 / HEAD / 实际模型 / 插件版本，排除仍跑旧目录或旧 AstrBot 插件。
- 复核内部注释不进入新回复 / TTS，历史仅在原 channel、through_id 和删除水位范围读取；不能用清空聊天、改写记忆或编造旧附件解决污染。
- 对照同一句话、不同前文的测试，验证语义选择而非词命中。保留纯文字、纯图、图文及先后顺序选择；不采用随机概率或硬性发图配额。

### B. 继续改善自然表达，优先普通聊天与主动发言

- 验证模型看见的候选是否足够贴切，避免满屏与语境无关的特殊用途图；检查召回、多样性、用户表达偏好和近期去重之间是否互相压制。
- 检查虚构格式示例是否被误当真实经历或让模型机械复制首张图；不允许样例落库、进入记忆或对话事实。
- 覆盖桌面、快捷输入、ASR、QQ 主人私聊 / 允许群回复 / 合规主动插话，以及 LLM 主动问候 / 心事分享。同一次 ChatController 生成，不另写 QQ 表情模型。
- 最终真实模型普通聊天已有自主配图，但主动问候样例仍选纯文字：这是允许的结果，**尚未证明主动配图自然度通过**。需要检验主动入口确实拥有同样候选 / 协议 / 发送能力，并用合理样例与真机体验验证；不要为了见到图片增加主动频率。
- 当前模型仍偶有“磨了这么久”等无依据措辞。只依据已有事实，自然不等于编造共同经历，不能把事实约束降级来换取口语感。

### C. 桌面与 QQ 端到端、发送事实

- 复验前置 / 后置、纯图、GIF、正文流式、语音打断、历史恢复、图片失效降级与角色切换 / 取消后旧附件。
- 重点审查 claim / ACK / 超时 / 幂等和权限撤销；不得把 yield chain 当 ACK，也不能把 accepted 当对方已读。不确定不自动重发。
- 实际 AstrBot 4.29.3 send API 已用合成 OneBot 验证，但真实 QQ 平台显示尚未验证。当前没有授权修改真实账号配置或向真实 QQ 群测试；先用隔离测试 / 模拟适配器，真实联调另由负责人安排。
- 用户回看旧占位文字可能误认为修复未生效；如改善旧消息展示，必须只作展示层降级，不能伪造历史附件或静默改写数据库。

### D. 图库交付、T13 与稳定性

- 仓库默认仍为 6 张原创图。80 张大肥鱼 GIF 是本机 D:/Study/AICompanion/output/t14-sticker-pack/manifest.json 的可选导入包，**不在本 PR 源码树中，也未自动安装到用户库**。远程 / 容器开发不能假设该 D 路径存在。
- 后续完善远程可复现的素材清单 / 许可证明 / 增量获取或合规打包交付；每项保留原作者 / URL / 原标识 / SHA / 许可，不为凑数引入未知授权。不能将代码 MIT / Apache 当作 GIF 许可。不得使用 Coopanion、其导出或衍生素材。
- 素材作者为“可以叫我云大人 / 雾雪”，GIF 按公开非商业署名条件；知识标注另为 CC BY-NC 4.0。证据：Loping151/dsh-nekowhale-stickers 的 b7b58c0ff347d639fabe87d90d6716c4b3cad185 / LICENSE-ASSETS。
- T13 未合入；图库根沿用 qq_meme_root。未来只适配 T13 最终接口，不新建根目录系统；跨盘资源与 revision 引用重映射、真实用户库升级还未联调。
- 之前发生过 Qt 原生退出 0xC0000005，后续复测通过但根因未定位。检查脚本已修复吞退出码问题并启用 faulthandler，继续保留首次失败证据，不能靠重跑抹掉稳定性问题。

## 5. 验证证据与审计标准

运行代码 4af066f：表情专项 76 项通过（5.464 秒）；全量 463 项，456 通过 / 7 跳过（34.737 秒）；Windows Smoke passed=true、remaining_workers=0。7 项为 Windows 上 POSIX 1、未安装可选 Playwright 3、隔离工作区无 Coopanion 的检查 3。

本轮真实 DeepSeek 使用合成对话和临时库，累计 43 次生成入口调用（含未改善的前期方案），无独立选图请求。最终对照见版本化证据 JSON：同末句“绷不住了”，搞笑语境选坏笑图，住院担忧语境纯文字；项目收尾分享选认可图；主动问候为纯文字。生成附件字节有效不等于 UI 显示或 QQ 送达，不能混写。

本机完整原始合成验证 / 日志 / 图片：D:/Study/AICompanion/output/t14-acceptance-fix-evidence/；初始交付证据在 t14-delivery-evidence/。这些目录不是远程可用资源，版本化 JSON 仅为无凭据、无用户聊天的摘要。之前一次测试库只读与 .env API 字段读取授权已经用于该轮定位 / 验证，**不是交给 Claude 的长期授权**；新真实模型验证须遵守 AGENTS.md 与负责人当轮授权。

建议命令（PowerShell，在实际 T14 根目录执行）：

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_expression*.py' -v
.\tools\run_checks.bat
```

虚拟环境 / 系统依赖不在 PR；容器按 pyproject 现有锁定依赖安装，不把 Windows 跳过项记为容器通过，也不把模拟 / 合成验证写成真实 QQ / 真实用户库通过。

Claude 每个补丁提交后更新设计 / tasks / implementation_status，在 PR 贴最新 HEAD、修改范围、命令 / 结果、尚未验证项及已知问题。Codex 在**该最新提交**审计架构、权限 / 历史隔离、解析 / 取消、发送状态和完整回归。未关闭的 P1 / P2 逐项处理或与负责人确认延期，不自行标可合并；最终由用户合并。

## 6. 不可突破的边界

- 一套 ChatController / 原 LLM 链路；一轮同次生成，表达与人格 / 记忆共用；不新增 Agent 或选图专用 LLM。
- 不改真实账号、不写真实用户库、不发真实 QQ 群测试、不复制 Coopanion；不擅合并 #10 / #11 / #12 / 本 PR。
- 原用户自定义人设 / 记忆 / 图库不能覆盖。T15 好感度与 T16 社区学习不属本次范围，未知关系保持中性 / 不自动跨渠道绑定；不能把 legacy trust 当陌生人好感。
- 五条长期架构约束：角色身份独立于模型；人格独立于记忆；关系跨渠道识别须关联及授权；表达习惯与关系状态独立于 Agent；用户可创作 / 导入 / 导出 / 切换人格但不能绕过系统安全边界。
- 此次负责人已授权 Codex 推送此任务分支并创建交接 PR；不授权推主分支、force push 或远程合并。
