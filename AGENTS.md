# AICompanion 协作规则（Codex 与 Claude 共用）

本文件是两个 agent 的共同约定。Claude 通过 `CLAUDE.md` 读取同一份内容。改这份规则也要走 PR。

## 项目一句话

Windows 桌面常驻的 AI 桌宠（Python 3.14 + PySide6 + SQLite）。默认角色是 DeepSeek 的二创鲸娘；会动（分层网格骨骼）、会记（衰减记忆、心事）、会说（千问声线、语音打断）、能干活（DeepSeek Harness 负责想，桌宠自己的工具执行并把关）。
计划书：`docs/project_plan_v3.md`；实际完成情况以 `docs/implementation_status.md` 为准。

## 分工

| 方 | 负责 |
| --- | --- |
| Claude | `src/aicompanion/agent/`、`ui/agent_*`、T6（十姿态形象包的编译与渲染）、T9（P7 桥接实现）、PR 审查 |
| Codex | T2、T5（已确认鲸娘十动作的素材整理与补画）、PR 审查 |
| 双方先在任务里说好再动 | `ui/main_window.py`、`storage/database.py`（表结构只由一方改）、`config.py`、`pyproject.toml` |

P7（QQ / B站）最后再做。任务清单见 `docs/tasks.md`，每个任务一份说明放在 `docs/tasks/`。

## 工作方式

- 一个任务一个分支，按任务命名：`task/<任务编号>-<主题>`（例如 `task/t2-mcp-sdk`、`task/t6-rig-studio`），不用 agent 名字做前缀；从最新的 `collaboration-baseline`（以后是 main）拉出。负责方写在 `docs/tasks.md` 里。
- 改动通过 PR 合入；另一方审查，用户最后合并。**不直接推送到主分支，不 force push 别人的分支。**
- PR 描述写清：做了什么、改了哪些文件、怎么验证的（贴命令和结果）、还有什么没验证。
- 开工前在 `docs/tasks.md` 把任务标成"进行中 + 负责方"，避免两边做同一件事。
- 公共文件冲突时，后合入的一方负责 rebase 解决，不要覆盖对方的改动。
- **可以合并的标准**：作者已处理全部 P1 / P2（或双方同意延后并写进任务说明），对方在最新提交上评论"审查通过"，作者也确认本地 / Windows 检查结果。满足后由**后确认的一方**在 PR 里写一条 `✅ 可合并（提交 xxxxxxx）`，并在给用户的汇报里列出；用户合并。草稿 PR 先转为正式再标可合并。

## 绝对不做

- 不读取、不打印、不提交 `.env`、数据库（`data/`、`*.sqlite3`）、`data/private-*`（私人立绘、试听）。
- Coopanion（`/Coopanion/`，AGPL-3.0，大肥鱼贴图另有版权）只在本机读取，**不复制它的代码，不提交、不分发它的贴图或由它导出的文件**。
- 不用 DeepSeek / Anthropic / OpenAI 的官方 logo 做应用图标或新设计的形象。角色都是二创，要能看出是原创设计。
- 例外（用户 2026-11-07 要求）：已确认的鲸娘设计及其对应标志保留，不为去标志重画角色。这不改变上面的 Coopanion 素材限制，也不表示获得第三方贴图的分发许可。
- 不做 `git push` 到主分支、`hexo deploy`、发布到任何公开渠道，除非用户明确要求。
- 删除文件优先移到回收区；批量删除、改动 `.git`、改系统设置先问用户。
- 环境注意：D 盘是 **exFAT，没有任何文件权限控制**，沙箱类保护在 D 盘不起作用，危险操作要靠审批。

## 环境与命令（Windows，项目在 `D:\Study\AICompanion`）

- 虚拟环境：`.venv\Scripts\python.exe`（Python 3.14）。可编辑安装：`pip install -e .[agent]`。
- 离线检查（每个 PR 必跑）：`tools\run_checks.bat` → 结果在 `output\v2-check\unittest.log`、`smoke.log`（`"passed": true`）。
- 改到干活（agent）时再跑：`tools\run_agent_checks.bat`（会调用真实模型，产生少量词元费用）。
- 性能：`tools\measure_pet.ps1`（内存目标 350 MB，目前约 440 MB）。
- 自检脚本不读 `.env`、不联网；真实模型的测试只用临时文件夹。

## 代码风格

- 界面文字、注释、文档用中文；说清"为什么"，不写空话。
- 新功能配单元测试（`tests/`，不依赖网络和真实模型）；桌宠可见行为在 `ui/pet_smoke_check.py` 加检查项。
- 不引入新依赖前先在任务里说明理由；依赖版本要锁定。
- 保持单进程结构：后台工作放 QThread / 线程，不阻塞界面。

## 记录与验收

- 每个合入的改动在 `docs/implementation_status.md` 末尾追加一段：做了什么、怎么验证、实测数据、**没验证的部分**。不把计划写成已完成。
- 每个阶段必须用户在真机上看过才算完成。
- 博客等整个项目完结后再写，期间不改 MyBlog。
