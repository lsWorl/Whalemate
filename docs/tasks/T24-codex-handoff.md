# T24 第二批交接：Codex 执行，Claude 审片

日期：2026-11-15。负责人要求第二批的生成、下载、记录和后期都由 Codex 执行。本文是接手清单。安排和理由见 [T24 任务说明](T24-whale-video-sprites.md) 的“2026-11-15 安排”，提示词见 [`art/deepseek-whale-video/prompt-templates.md`](../../art/deepseek-whale-video/prompt-templates.md)。

## 1. 当前状态（Claude 交接时，11:37）

- 工作区：`.worktrees/t23-q-whale-rig`，分支 `task/t23-q-whale-rig`。Claude 改过但**未提交**的文件：本文件、`T24-whale-video-sprites.md`、`prompt-templates.md`、`prompts.json`（选定侧身锚点 side-a）、`docs/tasks.md`。请核对后一起提交。
- 即梦账号已在 Claude 桌面应用的内置浏览器里登录；会话名为“Q版鲸鱼女仆待机动画设定”。剩余 **680 积分**，每段 Seedance 2.0 mini 720P 5 秒消耗 30 积分。
- 已提交、正在生成（都用 2.0 mini，首尾帧都是 `anchor-a-16x9.png`，5 秒，720P，比例“自动匹配”）：

  | 片段 | 提示词 | 说明 |
  | --- | --- | --- |
  | talk-v3、talk-v4 | 模板第 2 节（说话） + 通用约束 | 原计划 1 段，误提交了 2 段 |
  | happy-v1、happy-v2 | 模板第 3 节（开心挥手） + 通用约束 | 同上 |

  编号按 `clips/` 里已有文件往后排，所以说话从 v3 开始。哪段是 v3 / v4，按即梦里的生成时间先后定。
- **walk 还没提交**。输入框里已经填好模板第 4 节的走路提示词，但首尾帧仍是正面锚点，**必须先换图**。

## 2. 要做的事（按顺序）

1. **walk-v2**：在即梦里把首帧和尾帧都换成 `art/deepseek-whale-video/anchor/side-a.png`（同一份文件也复制到了 `Claude outputs/t24-review/side-a-侧身锚点.png`）。核对提示词是模板第 4 节原文，模型 2.0 mini、首尾帧、5 秒、720P，然后提交 **1 段**。负责人要求每个动作只生成 1 段，审片不过再重抽。
2. **下载**：5 段完成后，下载原片到 `art/deepseek-whale-video/clips/`，命名为 `talk-v3.mp4`、`talk-v4.mp4`、`happy-v1.mp4`、`happy-v2.mp4`、`walk-v2.mp4`。保留平台原始文件，不转码。
3. **记录**：在 `prompts.json` 的 `videos` 里各追加一条，格式与 idle-v1 相同。`prompt` 字段从即梦网页“详细信息”里复制**实际原文**，不要只写动作名（第一批就是这样记错的）。积分写 30；有种子就记，没有就写原因。
4. **后期**：按 T24 任务说明“第二批”第 2 步，用 `tools/video_pet/` 处理：每帧采样背景色，清空左上角“AI生成”水印区域，丢掉第 0 帧，降到 12 fps，只校正整体漂移。walk 从 0～4 秒里截取整数个步伐周期。输出到 `output/t24-pack/`，并生成审片材料（GIF、首末帧并排、脸部一致性拼图），报告写明首末帧差和偏绿像素数。
5. **通知审片**：在 PR 评论里列出产物路径和每段的度量，交给 Claude 审片。两段说话和两段挥手都要处理，由 Claude 选用哪一段。

## 3. 边界

- 只花第 2 节第 1 步那 1 段 walk 的积分。重抽要等 Claude 审片后再定；不开会员、不购买积分。
- 不改播放器、`ui/main_window.py`、`config.py`、`pyproject.toml`；不替换默认形象。
- 视频与锚点按 T24 的“许可与提交”处理；图集产物放在 Git 忽略的 `output/` 下。

## 4. 2026-11-15 12:30 更新：第 1～3 步已完成

- walk-v2 已由 Claude 在内置浏览器提交（首尾帧为 side-a 重建图，种子 1717390052）；5 段由 Codex 下载，Claude 改名放进 `clips/`：talk-v3、talk-v4、happy-v1、happy-v2、walk-v2。`prompts.json` 已追加 5 条，提示词是实际粘贴的原文。剩余 650 积分。
- Claude 审片结论见 T24 任务说明“第二批审片”：采用 **talk-v3、happy-v2、walk-v2**（walk 循环取第 9～33 帧）；talk-v4 不采用；happy-v1 备选。
- **Codex 接下来只做第 4、5 步**：只对这三段做后期，输出到 `output/t24-pack/`，然后请 Claude 看报告。

## 5. 2026-11-15 12:45 更新：第 4、5 步已由 Claude 完成

负责人要求 Claude 全程执行。后期、组包、审片记录都已完成，见 T24 任务说明“第二批后期”。Codex 只在 Windows 上代跑了命令：`tests.test_animation_pack` 和 `tests.test_video_pet_idle` 共 24 项全部通过；`--animation-pack` 只能配合 `--preview` 使用，所以没有单独的 smoke 报告；5 分钟可交互预览已启动（`output/t24-pack-b2-preview/stdout.log`）。剩下只有负责人真机看预览、给出结论。所有改动都还没有提交。
