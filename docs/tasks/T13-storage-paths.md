# T13：记忆与表情包目录

负责：Codex。用户要求增加可选择记忆与表情包磁盘的路径，不默认放在 C 盘。

在设置窗口新增独立“存储位置”页，分别选择聊天 / 记忆数据库目录和表情图库目录。路径记录在程序旁的 `.aicompanion-paths.json`（只含目录，不含 API 凭据，Git 忽略）。开发版默认项目的 `data/`、`data/memes/`；程序在系统盘时默认选择可用的非系统磁盘（优先 D 盘），找不到时明确要求选择目录，不静默回退 C 盘。

记忆和聊天、偏好、设置共用 SQLite，不能仅移动一张记忆表；更改目录下次启动生效，旧实例退出后使用 SQLite backup 复制一致快照，不覆盖目标既有数据库，不删除旧库。复制 / 校验失败时拒绝启动空库，旧数据保留。表情可以选择已有分类图库，或新建目录填入仓库自带原创表情，不移动 / 删除外部素材。

共享文件范围：本任务仅 Codex 修改 `config.py`（解析可选路径记录）、`app.py`（正式启动前准备迁移）、`ui/settings_window.py`（新页面挂载）；不改 `storage/database.py`、main_window 或表结构、依赖。已在 PR #10 下同步 Claude 分工；T12 媒体仍通过既有 `qq_meme_root` 设置取图库，后合入方处理文档 / 页面冲突。

验证仅用合成 / 临时数据库，不读取真实 `.env` 或用户数据。覆盖旧数据完整复制、失败保留旧库、目标冲突拒绝、路径持久化、打包 / 开发默认、设置页选择和保存及完整离线检查。

结果：20 项存储专项回归通过；完整 `tools/run_checks.bat` 349 项通过（跳过 4 项），桌宠自检通过且无剩余 worker。实际用户库迁移、真实跨盘权限 / 磁盘耗尽 / 断电与打包运行尚未验收，未执行任何真实用户数据迁移。操作见 `docs/storage-locations.md`。


## 2026-11-09：更新目标分支并处理冲突（Codex，已验证待复审）

负责人要求解决 PR #11 的合并冲突。使用干净独立工作区，将最新目标 collaboration-baseline / 2e8ee1145e65cf317153e6becd31aaecb9e3508d 整合进 T13 / 778a857；目标已合入 PR #10 与 #12，不重写远程历史，不修改主分支。

冲突记录：

- docs/tasks.md：保留 T13、T12 和长期架构约束任务，T12 按实际合并记录更新为已合入。
- docs/implementation_status.md：双方实施记录均保留，不用一方覆盖另一方。
- ui/pet_smoke_check.py：保留 storage_locations 与 qq_media 两个检查及各自的必过项，角色 / 语音 / agent 等旧检查继续保留。

本轮不修改存储迁移实现、表结构、真实路径记录 / 账号 / 数据库，不将“无冲突”当作“审查通过”。Claude 对 778a857 提出的默认补入图片中文含义 P2，以及外接盘识别等 P3 仍须独立处理 / 复审，不借冲突处理静默扩展功能或改动 PR #13 的授权识别工作。验证结果见下。


验证：20 项存储专项通过（1.055 秒）；8 项 QQ 页面流程通过（3.092 秒）；最终 tools/run_checks.bat 共 407 项，400 通过 / 7 跳过（28.504 秒）。单测日志 exit=0，Smoke 日志 exit=0，passed=true、remaining_workers=0，storage_locations_ready 与 qq_media_settings_ready 均为 true。跳过项为 Windows 的 POSIX 进程组 1、可选 Playwright 3、隔离工作区未提供 Coopanion 的检查 3。

首次全量有 1 项 QQ 保存诊断测试失败（bridge_off 而非 start_failed），针对性重跑通过；检查发现测试把“原端口 + 1”当作空闲端口，现改为 free_port 并显式断言服务启动，避免测试前提被绑定失败覆盖。没有改 QQ 运行代码，首次失败和后续日志保留在 output/pr11-conflict-check。存储迁移 / config / app / settings_window 的实现与原 T13 提交保持一致，本轮没有读取真实 .env / 用户数据库 / 账号或执行真实迁移。

使用普通 merge 提交同步目标分支，保留已审查的 778a857 历史，正常 fast-forward push，不 force push。没有合并远程 PR；无冲突不等于审查通过，待 Claude 处理 / 确认既有 P2 后再在最新提交复审。
