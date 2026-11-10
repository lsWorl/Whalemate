# 原鲸娘透明逐帧动画

角色设计沿用本项目 `art/deepseek-whale/originals` 的已确认鲸娘十姿态。待机、说话、思考由内置 image_gen 以各自原画为参考新绘 18 张关键帧，经过脚底对齐和双向光流离线补间；其余动作保留既有原画，仅做小幅连续形变。

本目录不包含 Aboutme、dsh-pet、Coopanion 的图片、代码或音轨。Aboutme 仓库只作为透明精灵图播放方式的参考；播放器、校验器与离线工具均为本项目实现。

图集 384×384 / 帧，24 fps，8 列。共 13 个动作、960 帧：待机 / 思考各 96 帧，说话 48 帧，其余各 72 帧。三组新绘原始图纸、归一化关键帧、完整提示词及对齐记录位于 `art/deepseek-whale/animation-keys-v1`；`SOURCE.json` 记录各输入和输出的 SHA256，明确区分新画与程序补间 / 形变。

运行时后台解码当前一个图集，不需要 NumPy、Pillow、联网或视频模型。隐藏后停止播放并释放图集。右键 →「动画动作」点播；「形象」可切回原分层骨骼或原立绘。解码失败自动回到原立绘，角色身份、人格、记忆和语音设置沿用原有数据。

重建：`.venv\Scripts\python.exe tools\build_original_animation.py`。只在离线美术编译时使用锁定在 `tools/original_animation_requirements.txt` 的 Pillow / NumPy，不改运行依赖。

本轮只补了三组常用循环的关键帧；其他动作仍是原画的小幅运动，不声称完成全套大幅肢体动画。说话动画采用预绘口型，未实现音素级口型同步。新绘一致性、运动节奏和长期运行仍需用户真机验收。

## 动作之间的过渡

另有 22 段一次性过渡（共 200 帧），覆盖待机与 11 个动作的往返。站→工作、站→抱鲸犯困及反向各 14 帧，其余各 8 帧；24 fps，单段约 0.33～0.58 秒。两个非待机动作之间最多经过两段，不无限排队。手动点播的播放时长从进入目标循环后开始。

站坐新增八张中间原画，来源见 `art/deepseek-whale/transition-keys-v1`。其他短过渡使用当前两端原画的轮廓引导变形，并非额外手绘肢体动作。`TRANSITIONS.json` 独立记录其输入、输出与来源，主循环的 SOURCE.json 不变。

播放器提前在后台准备下一段；没有准备好时保留完整末帧。连续切换从正在显示的画面接续，丢弃旧目标；拖拽 / 下落优先用短过渡响应。过渡中的透明命中按显示画面计算。短淡入采用预乘颜色相加，避免 SourceOver 双层透明度把身体变淡。


## 性格动作、低步幅与连续切换

当前 20 个动作 / 1368 循环帧、36 段过渡 / 372 帧。66 张主循环新绘关键帧与 20 张过渡原画（含先前批次），并非所有运行帧手绘。六个新增动作对应独立表情 / 日常词汇，低步幅正反步态通过离线腿层重建，最大抬升 5 px，替换初稿高抬腿。

相容站姿由 blendGroup 声明，直接混合且延续相位；淡出期间旧循环继续播放。取奶茶、伸懒腰、转身的新中间姿态编为 0.75 秒片段。cycleDistance 表示参考显示尺寸下一个步态周期的移动距离，配合有制动的二维路线。

工具：tools/build_personality_animation.py、tools/low_step_walk.py、tools/build_pose_transitions.py；完整 build_original_animation.py 自动串联。原始图纸 / 完整提示词在 art/deepseek-whale/personality-keys-v1、locomotion-keys-v2、transition-keys-v2。技术参考见 docs/tasks/T20-animation-references.md。

本轮完整检查 706 项：699 通过 / 7 跳过，Smoke 通过。最终真机审美 / 多屏 / DPI / 长期检查待完成。
