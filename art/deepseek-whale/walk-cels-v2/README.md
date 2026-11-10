# 鲸娘整帧行走补画

用户要求增加完整原画以改善流畅度。本批左右各补17张完整人物过渡姿势，插入已保留的17张原画之间，形成34张/周期。旧原画PNG保持像素不变，新增帧由内置image_gen绘制，完整提示词见prompts.json。身份沿用用户原始参考；pair-guides是相邻原画A/B的对照，cleanup-input是新帧统一画风前的整组输入，最终left-sheet.png / right-sheet.png为新补的中间姿势，每侧取前17格。

运行时序为34×7+2=240帧，24FPS、10秒；关闭循环的两帧复用首张原画，保持首末一致。相邻原画之间补一张，不通过复制旧帧假装增加原画，也不拆手臂/腿或用光流生成新画。原17张放在../walk-cels-v1/left与right；本批left/00..33.png中偶数为原画，奇数为新画。sequence.json记录帧顺序、来源、哈希和整帧等比对齐参数。

为避免240帧重复图集超过原32M像素限制，运行WebP只存34张完整原画（8列5行），清单frameMap按240帧时序引用它们。结构验证仍保留原像素限制，索引必须是有效整数且逐逻辑帧提供；播放器当前帧、淡出旧帧、解码构图均使用映射。每侧物理图集为5,898,240像素，约22.5MiB RGBA；不代表整个应用内存。默认动作无frameMap时兼容原有格式。这是项目的帧引用扩展，配套JSON含FrameMap。

编译tools/build_walk_cels.py；总编译tools/build_reference_motion.py支持行走24FPS/240帧，互动保持12FPS/120帧。仍按距离驱动行走，10秒行走距离参数280不变，300px显示宽度名义步速28px/s。八个互动、角色身份/语音/人格与旧版回退不调整。图集逐帧像素、旧原画不变、24FPS播放、索引边界和混合切换都有离线验证。

交付仍放output/reference-motion-v1，使用walking-pair-24fps.gif、walking-before-after.gif、walking-preview-24fps.html、all-actions-dense.gif和DeepSeek-10-actions-walk24fps.zip；共1440张PNG（左右各240，八互动各120）。旧版10秒120帧预览和包保留。是否足够自然、细节是否稳定仍需用户看连续预览确认。
