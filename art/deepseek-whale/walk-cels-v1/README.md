# 原鲸娘整帧行走（2026-11-10）

本批按用户“不要分开画手臂和脚，直接按帧画走路”的要求制作。唯一角色参考为 ../reference-motion-v1/user-identity-reference.png；使用内置 image_gen，完整提示词与输入关系见 prompts.json，不读取密钥、不训练模型。

最终左右各一张 3列×6行完整人物原画 left-sheet.png / right-sheet.png。每格是头发、躯干、双臂、双腿、裙摆与鲸尾一起绘制的完整姿势；不是把手脚原画分离后用骨骼驱动。pose-guides 是供整帧绘图使用的姿势示意，cleanup-input 保存全周期清稿前的完整画面，仅供复现与审计，不参与运行。分层实验和未采用草稿已回收到 output，不进入本批素材。

每侧取前17张独立完整人物原画，按顺序播放7个循环，再复用第一帧闭环，17×7+1=120帧。最后一格为闭环姿势审图稿；成品复用第一帧消除端点重画误差。17张独立原画不是120张不同的新画。编译只对完整人物裁格、统一等比缩放、水平居中及接地线对齐；不做肢体切分、关节扭曲、整图光流或插值造帧。

运行导出384×384、12FPS、10秒、120帧；图集8列15行，配套walking_left.json / walking_right.json及原聚合animations.json。整帧图集组织与逐帧播放参考[aboutme-pets](https://github.com/hunmer/aboutme-pets)，未下载/使用其角色贴图、音轨或复制实现代码。桌面步速由图集距离和时长计算，当前300px显示宽度下28px/s；旧版回退保持原行为。

重建 tools/build_walk_cels.py；tools/build_reference_motion.py 和完整 tools/build_original_animation.py 会使用这两张最终图纸。八个互动动作不重画。每侧normalized PNG与sequence.json记录实际使用的原画、统一缩放、帧顺序与哈希。单元测试逐像素比对每个运行帧与整帧原画，防止重新引入拆肢体/光流步态。

艺术验收仍需用户观看：行走是否自然、面部视角与头发/服装细微一致性、前后腿遮挡与脚底接触。这里是有限整帧原画的循环序列，不声称使用视频生成模型或动作捕捉；自动检查不能替代艺术验收。
