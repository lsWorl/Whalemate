# 已确认鲸娘十姿态源包

格式：`AICompanionPosePack / 1`；原版本保留为 `source_version: pose-layered-v2`。

本包沿用用户确认的原鲸娘、十动作和对应标志。这里只搬运本项目已有的 ImageGen 原图与确定性裁切切片，并整理可移植清单；未重画、缩放源像素，也未复制 Coopanion 的代码、贴图或导出文件。

## 文件

- `model.json`：十姿态合并索引；每份内嵌姿态与 `poses/<姿态>/model.json` 相同。
- `poses/<姿态>/cropped/`：184 个基础切片、33 个表情切片，共 217 张 RGBA PNG。
- `originals/`：16 张确认原图，原始 PNG 字节及 SHA-256 保留。
- `poses/talking/combined-expression-reference.png`：由已有差分组合的闭眼加闭嘴参考；因此合计验收 17 个状态。
- `state-map.json`：状态、眨眼及口型的对应关系。
- `sources/`：原画、修正和分层标记的提示词记录，以及全部 234 张源图片的 SHA-256。历史提示词中的本机绝对路径已去除，分层辅助标记不作为角色贴图提交。
- `qa/validation.json`：本次独立重算的 17 状态回拼结果；源画布 1254×1254，可见 RGBA 与 Alpha 差异均为 0，连 alpha=1 的变化也会检查。

全画布图层副本未重复提交。`file`、`crop_file` 与 variants 的 overrides 均指向实际存在的裁切 PNG；按 `box: [x,y,width,height]` 贴回原画布，并按 z 叠放。`grabbed` 的 anchor 是后领挂点 `(724,497)`，其他姿态保留 `(627,1156)`。

## 验证与编译

在项目已安装 Pillow 的环境运行：

```powershell
.venv\Scripts\python.exe tools/verify_pose_sources.py art/deepseek-whale
```

T6 的编译器在 PR #2；合入或检出其代码后运行：

```powershell
.venv\Scripts\python.exe tools/rig_studio.py compile-poses art/deepseek-whale output/whale-runtime --verify
```

本批已用 PR #2 的 `2578bb7` 在 Windows 编译验证：10 姿态、184 基础层、33 替换层，17 状态回拼零差异。编译结果只放本机输出目录，运行时约 4.4 MB 的提交由 Claude 负责。

## 运动范围

当前仍为可见像素切片；遮挡面的补画与 fin / ahoge 独立支点尚未交付，所有姿态的 `motion_ready` 显式为空。鳍、呆毛和尾巴的 40 项局部摆动应保持禁用，不能因为已有 tail 节点就启用旋转。

这批交付用于现有姿态切换、整体动作及经验证的小幅变形。大幅改变肢体姿势需要另行补全遮挡面；补画、根部支点以及用户对动画观感的真机验收仍未完成。

当前文件夹连同历史侧向草稿与预览约 31.1 MB（29.7 MiB），源图片约 29.1 MB。未提交 217 张全画布冗余图；本批使用普通 Git，后续素材增长时再在 PR 评估 Git LFS。
