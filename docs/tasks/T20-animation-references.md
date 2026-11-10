# T20 动画连续性参考（2026-11-10）

用户要求参考其他实现，并允许为连续动作重构原图，保留大肥鱼特征。

- Aboutme 资源仓库：[README](https://github.com/hunmer/aboutme-pets)。它将透明 WebP 图集与逐动作 JSON 分离，预览页以 12 fps 播放；这说明图集格式负责播放和打包，本身不能保证动作之间有合理的中间姿势。本轮只参考组织方式，不复制人物、音轨或代码。
- Godot 官方：[Using AnimationTree](https://docs.godotengine.org/en/stable/tutorials/animation/animation_tree.html)。状态机支持即时、同步和播完后切换；Sync 保留播放位置，Xfade Time / Curve 控制混合，2D 逐格动画可用 Carry 保留相位。本项目据此为相容站姿声明混合组、接续归一化相位，避免先回待机或每次强制从第一帧开始；不引入 Godot。
- Spine 官方：[Applying Animations](https://esotericsoftware.com/spine-applying-animations)。AnimationState 管理动画切换、队列、混合。本项目只采用旧动作继续推进再与目标混合的思路，不使用或复制 Spine 的骨骼代码，也不把整图混合说成骨骼混合。
- Qt 官方：[QEasingCurve](https://doc.qt.io/qt-6/qeasingcurve.html)。缓动控制插值进度；播放器保持 smoothstep 混合，移动采用有上限的加速度与接近目标时制动，避免一步就达到全速或瞬间停下。

本次修正：

1. 高抬腿草稿撤下，低步幅图纸保留鲸娘脸型、长发、鳍耳、尾巴、呆毛、长袖女仆装与鲸鱼标志。腿部离线分层，小步、近直膝，抬脚最大 5 px，保留上身新画。
2. idle / talking / thinking / 得意 / 气鼓鼓 / 害羞 / 惊讶 / 开心 / 挥手为相容站姿；直接实时混合，归一化相位延续。混合时旧图集继续按时钟推进，到期立即释放；打断时从当前显示图续接。
3. 取奶茶、伸懒腰、转身分别补四张中间姿势，坐下工作 / 抱鲸仍使用上一轮补画。中间片段小段柔和混合，端点无损匹配实际循环。没有声称全部中间帧均为手绘或真实骨骼求解。
4. 新动作、二维路线与艺术审查继续放在 PR #25；测试、性能和未验收项以任务主记录的最终追加段落为准。

## 整帧行走的实现参考（2026-11-10）

- 重新核对 [aboutme-pets 转换脚本](https://github.com/hunmer/aboutme-pets/blob/main/convert_video_pet_spritesheet.py)：它按12FPS从完整动作视频采样，以8列拼成透明WebP，并分别输出 FrameWidth / FrameHeight / Columns / Rows / FrameCount / SampleFps 等JSON元数据。
- [预览页](https://github.com/hunmer/aboutme-pets/blob/main/index.html)按当前帧序号计算行列，截取完整画面绘制，并提供暂停、逐帧和进度滑杆。它不通过把手臂或脚拆开来产生动作。
- 本轮采用同样的整帧图集组织和按帧播放思路，自己用原始用户参考绘制整个人物姿势。输入是整帧原画，不声称复现了对方的视频生成模型；没有下载 / 引用其人物贴图、音轨或复制实现代码。
- [Toon Boom Walk Analysis](https://learn.toonboom.com/modules/walk-cycle-animation2/topic/walk-analysis)用于审图：触地、下沉、经过、抬升后交换两腿，两个触地姿势步幅相近。该知识用于绘图姿势，不要求继续采用本轮已撤下的分层骨骼实验。
