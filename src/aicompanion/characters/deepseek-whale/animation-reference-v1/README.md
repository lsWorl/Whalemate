# 唯一参考十动作运行包

由 tools/build_reference_motion.py 从 art/deepseek-whale/reference-motion-v1 构建；无需模型API、密钥或用户数据。每个请求动作 120 帧 / 12 FPS / 10 秒，384×384，8列15行无损WebP图集；另含统一站姿。

animations.json 可独立由 AnimationAvatar 播放。SOURCE.json 记录参考 / 图纸 SHA256、缩放、边距、首尾和时长。所有八个互动的首末帧与 poster.png 完全相同，左右行走首末为同一行走相位。

旧 animation-original/animations.json 通过 replacementPack 指向本目录。新默认表达在动画层覆盖对应动作；其他旧动作只做等比构图适配，未重写人格、关系记忆、Agent 或语音。用户可在右键“动画版本 → 旧版动画（回退）”切回稳定旧版，设置按角色保存。

边界：关键姿态由 image_gen 绘制，其余是离线补间，不等于1200张手绘或实时骨骼动画。本批只承诺已请求的左右行走方向；旧版二维走动保留在回退模式。用户仍需确认细节一致性及运动自然程度。
