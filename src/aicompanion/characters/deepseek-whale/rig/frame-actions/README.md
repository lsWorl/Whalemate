# 鲸鲸 P1 整帧动作资源

来源：用户已确认的 `art/deepseek-whale/approved-poses-preview.png`、同套 idle 与经 Claude 审帧的整身动作草图。使用内置 imagegen 绘制完整角色；不含 Coopanion 代码、贴图或导出物。

`source/` 保存 512×512 运行尺寸的无损透明完整画帧，共42张（包含允许的转身整张镜像）。拖动右向4帧复用左向图集并镜像，因此13种动作共46个播放帧。1254×1254 审帧 PNG 留在忽略的 output 目录，逻辑画布与脚底锚点仍按1254px记录。没有部件拼接或网格变形。

`actions.json` 包含帧率、帧数、播放顺序、脚底与抓点锚点、头部轮廓尺寸标尺、源帧路径。整帧以不透明过渡关键帧衔接；悬挂换向经过两侧回中帧各70ms。走跑由实际移动距离推进；其 fps 是独立预览的名义帧率。`head_width_qa` 的数字是固定头型的脸颊轮廓标尺，用于一致缩放完整画帧，不代表发尾/鳍耳的全宽。

图集每格512×512，4列；仅做完整图片等比缩小与打包。源图可离线重建图集：

```powershell
.venv\Scripts\python.exe tools\compile_motion_frames.py --rebuild src\aicompanion\characters\deepseek-whale\rig\frame-actions
```

原始生成 PNG、逐次提示词、关键帧联系表、逐动作 GIF 与真实 Qt 窗口事件回放位于用户指定的 `output/t24-motion-review/`，由 Git 忽略。历史部件方案另存于该目录 `retired-parts/`，没有提交到此形象包。
