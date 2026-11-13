# V4-7f 跨天办事

Codex 实现，Claude 审查。起点为 collaboration-baseline 的 9549bd7，分支为 task/v4-7f-long-tasks。

## 已有能力

V4-5 已能在退出时保存暂停任务和待开始提议。启动会把中断的运行改为 paused。操作日志保存原目标、工具、审批、结果和用量。接续通过条件更新领取同一任务，写入、移动、删除和有副作用的命令在动手前先经 _before_effect / reserve_agent_effect 留指纹。重复的副作用在接续时被拒绝。

V4-7c 已随任务保存附件清单、识图结果及副本路径，暂停时保留副本，终态按回收区规则收走。V4-7b/e 已有主人 QQ 账号与机器人绑定、平台消息编号防重放、桌面与 QQ 共享的逐项确认，以及接续时不可批量放行的审批。

## 本次补上

- schema 21 在原任务上增加工作区、计划、暂停原因、暂停轮次。新增只追加的检查点与副作用说明。v20 升级保留旧目标、步骤、结果、用量、附件和原指纹。旧任务没有产物凭据，接续时由 Harness 先核实当前状态。
- Harness 的公开计划和已结束的工具阶段由程序入库。每个检查点包含本地时间、完成事项和产物清单。文件记录绝对路径、字节数、SHA-256，目录记录路径和类型。移动或移入回收区时，旧路径退出当前清单；历史检查点仍可回查。
- 新 MCP 工具 task_stage 接收计划、阶段产物、所缺材料和最终交付声明。写文件、编辑、建目录、移动以及 PowerShell 声明的 outputs 在操作返回时自动留存检查点。命令事先不知道的新文件名可以在该阶段用 checkpoint 补登。最终 deliver 只能引用已登记产物，程序会核对当前保留的全部产物。纯文字交付使用空文件清单。
- 文件按流计算 SHA-256。交付时再检查路径、普通文件类型、大小和 SHA-256。JSON、DOCX、XLSX、PDF、PNG、JPEG、WebP 还要由对应解析器实际打开；这些格式的单文件打开校验限 32 MiB。目录核对存在性和类型，交付的具体文件需要逐项列出。核对失败或缺少交付清单时保留暂停任务，将原因和相关路径显示给主人。
- 等材料、审批超时、手动停止或程序退出各有暂停原因。设置 → 办事 → 操作日志可查看计划、进度、产物哈希和最终结果，填写补充材料路径或剩余事项，确认接续，或者放弃并回收附件。接续使用原目标和原工作区，输入包括检查点摘要、产物和已尝试的副作用。已尝试但尚未记录完成的操作必须先核实，指纹继续拦截重放。
- 过了暂停当天，统一主动调度器可在主人侧询问是否继续。同一任务每天最多展示一次，沿用主动上限、间隔、安静时段和等待回复约束。先占位，显示前持久确认；拒绝、超时、重启均不会自动开跑。桌面和 QQ 共用同一项接续确认。
- 由 QQ 交办的任务暂停后会收到“任务号-暂停轮次”。主人可在原绑定私聊回复“继续 12-1”或“放弃 12-1”。桥和桌面消费指令时均核对机器人、主人、角色、任务来源和暂停轮次。旧轮次或重复平台消息无法控制下一次暂停。沿用现有插件协议。

## 文件归属

| 范围 | 改动 |
| --- | --- |
| agent/checkpoints.py | 阶段记录、恢复摘要、产物快照与最终核对 |
| agent/dsh_backend.py | ACP 公开计划和工具完成事件 |
| agent/tools_server.py、agent/mcp_server.py | 本地工具完成回调、命令产物和 task_stage |
| ui/agent_session.py | 接续审批、等待与暂停、结果核对、QQ 状态通知 |
| ui/agent_pages.py | 操作日志中的任务列表、计划、检查点、材料和控制按钮 |
| 其他共享文件 | storage/schema.py、storage/database.py、brain/scheduler.py、bridge/service.py、bridge/tasks.py、ui/main_window.py、ui/settings_window.py |
| 验证 | tests/test_long_tasks.py、tests/test_dsh.py、tests/test_scheduler.py、tests/test_qq_tasks.py、tests/test_task_attachments.py、ui/pet_smoke_check.py |

## Windows 验证

- `tools\run_checks.bat` 退出码 0。1383 项单测，用时 242.474 秒，失败 0，跳过 7。
- `tools\run_agent_checks.bat --offline` 退出码 0。1383 项单测，用时 241.681 秒，失败 0，跳过 7。
- 两次桌宠 smoke 的 `long_task_checkpoint`、`passed` 均为 true，`failed_checks` 均为空。离线模式跳过真实模型步骤。

未使用线上 Harness、真实 QQ 账号或 NapCat/AstrBot 验证；跨日、异常退出和副作用重放使用可控时钟、临时库与模拟运行器验证。
