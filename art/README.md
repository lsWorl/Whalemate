# T5 鲸娘美术

当前只推进先前已确认的 DeepSeek 二创鲸娘，沿用原有十动作与对应标志。设计和验收参考见 [十动作预览](deepseek-whale/approved-poses-preview.png)，具体范围见 [T5 任务说明](../docs/tasks/T5-character-art.md)。

该预览来自本项目此前生成的原创动作素材，不是 Coopanion 的贴图或导出文件。原图、裁切图层和正式源清单现已整理进 [鲸娘源包](deepseek-whale/README.md)，独立校验的 17 个状态可见 RGBA / Alpha 差异均为 0。遮挡补画、独立支点及最终动画验收仍未完成。

## 保留的历史草稿

| 文件 | 当前状态 |
| --- | --- |
| `deepseek-whale/reference-v1.png` | 侧向重设计草稿，已被原鲸娘十动作基准取代 |
| `claude-reader/reference-v1.png` | 读书少女草稿，暂缓后续制作 |
| `chatgpt-dragon/reference-v1.png` | 白龙草稿，暂缓后续制作 |

这三张由内置 ImageGen 生成，提示词、来源和实际尺寸分别见 `prompts.json`、`concepts.json`。保留原始文件供追溯，不是当前交付目标。原生成尺寸为 1254×1254 RGBA；读书少女下角有 alpha=1 的微弱残留。

用户已允许 Pillow 做确定性的拆分、缩放、透明画布摆位和合成检查；原画、补画与新表情绘制仍使用 ImageGen。不会为去除已有标志重画当前鲸娘，也不引入 Coopanion 的代码、贴图或导出文件。

对外发布许可尚未指定，发布前由用户确定。
