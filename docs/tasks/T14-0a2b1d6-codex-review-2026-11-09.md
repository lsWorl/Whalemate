# T14 0a2b1d6：Codex增量审计（2026-11-09）

审查期间PR #13从726c401更新到0a2b1d627766427f6edd5efc75aad6c701a88c3a。本报告与[726c401完整审计](T14-726c401-codex-review-2026-11-09.md)一起阅读。Codex未改Claude运行代码；当前仍不审查通过、不标可合并。

## 新增P2：自动审核覆盖用户停用与自定义语义

catalog.py:155-159只检查pending_review，metadata.update(original)覆盖旧语义等标注，随后无条件enabled=1，违反本提交的“已停用不动”和不覆盖用户自定义内容要求。

合成图库稳定复现：将仓库celebrate.png复制进临时图库，用“用户自定义：仅作纪念，不要发送”登记待审核，执行enable(True)再enable(False)模拟用户明确停用，scan之后从pending_review / enabled=0变为approved / enabled=1，语义变成“开心、庆祝、赞同”。原文保存语义和用户停用选择均丢失。测试仅覆盖rejected + disabled，没有覆盖pending_review + 显式停用。

请将内容许可识别与用户启用选择分开，补充可信来源时保留明确的用户停用及自定义语义。待审核默认禁用与用户主动停用目前均为0，需要正确区分，不能用无条件重新启用处理。请覆盖首次默认安装、待审核迁移、用户明确停用、用户自定义语义、已拒绝与普通非内置素材，确保并行扫描也不覆盖新改的用户设置。

[合成探针记录](evidence/T14-0a2b1d6-original-disable-proof.json)，没有操作真实图库 / 数据库。

## 已有两项P2仍然适用

0a2b1d6只改catalog / 文档 / 新测试；与726c401逐字节核对，expression/service.py和AstrBot插件main.py均相同，因此QQ文字失败后续发图片、delivery查询与trace插入之间的竞态仍适用。复现记录与修复要求见前报告，不重复调用模型验证未变化的代码。

## 模型与评分边界

round7样本仍是2045050；独立42合适 / 17勉强 / 0不合适。最近8轮真实连续对话针对726c401：实际显示4，纯文字3，history_echo拒绝1；慰问缺候选却发Zzz的质量问题未因本次内置图补元数据而证明解决。未对0a2b1d6再次调用真实模型；不把先前结果冒充当前HEAD真实行为验收。QQ实网仍未测。

## 离线检查

最新0a2b1d6隔离Windows快照：完整512项（505通过 / 7跳过，39.007秒），Smoke passed=true / remaining_workers=0，两个进程退出0。[执行记录](evidence/T14-0a2b1d6-offline-checks.json)。上述新P2探针不在原512项覆盖范围内；全量绿灯不构成放行依据。用户真实数据、账号配置与原图库没有变更；所有模型驱动经stdin执行，无临时模型脚本文件。
