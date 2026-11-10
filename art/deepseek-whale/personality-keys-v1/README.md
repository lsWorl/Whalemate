# 性格与日常动作关键帧

内置 image_gen 依据本项目原鲸娘绘制六组新动作：叉腰得意、抱臂气鼓鼓、害羞揪袖、惊讶后仰、捧奶茶偷闲、伸懒腰；每组六张，共 36 张。原始图纸、单帧、完整提示词见本目录，输入 / 输出哈希在运行素材 SOURCE.json。

初版高抬腿的正反步态已退到本机 output/personality-first-check/high-step-art，不接入编译和 PR；新的正反低步幅关键帧见相邻 locomotion-keys-v2。人格卡 description / 共同记忆 / 语音不变，仅扩展 renderer.vocab。

离线重建：`python tools/build_personality_animation.py`。局部重编：`--only walking walking_back`。未来完整 `build_original_animation.py` 会串联本编译器及过渡编译器。
