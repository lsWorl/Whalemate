# T6：自己的形象包——格式、`rig_studio` 与渲染接入

负责：Claude（代码）　出图：Codex（T5）　审查：互相　分支：`task/t6-rig-studio`

本文第 1–6 节是 **T5 出图和 T6 代码共同遵守的规格**，改规格要在这个文件里改并在 PR 里说明。

## 0. 目标

现在的骨骼形象借用本机 Coopanion 的大肥鱼（只能本机用，不能分发）。T6 让我们自己的分层素材也能变成同一种骨骼模型：

```text
art/<角色>/                  分层源图（全画布对齐的 PNG）+ pack.json（支点等标注）     ← T5 出图，T6 标注
   └─ tools/rig_studio.py 编译 →
src/aicompanion/characters/<角色>/rig/rig.json + textures/   AICompanionRig v1（运行时）← 渲染器直接读
```

渲染器 `assets/rig/rig.js` 和动作引擎不分素材来源：只要编译结果符合 v1，大肥鱼能做的动作（甩头发、裙摆、腿、尾巴、头部视差、眨眼、口型、拎起、摔晕……）自己的形象全部都能做。

## 1. 两种格式与版本

| 格式 | `format` / `version` | 谁写 | 放哪 |
| --- | --- | --- | --- |
| 源素材清单 | `AICompanionPackSource` / `1` | T5 出图 + `rig_studio` 标注 | `art/<角色>/pack.json` |
| 运行时骨骼模型 | `AICompanionRig` / `1`（沿用现有版本号） | `rig_studio` 编译生成，不手改 | `src/aicompanion/characters/<角色>/rig/rig.json` |

运行时 v1 只做**向后兼容的增补**（新增可选字段：`shy_*` / `smug_*` 五官、`talk_mouth`、`source.kind: "pack"`），旧的大肥鱼缓存照常能读。以后要做不兼容改动时 `version` 升到 2，并同时更新 `rig.js`、`rig_import.py`、`rig_studio`。

## 2. 画布与坐标（出图必须遵守）

- **每个图层都是一张 2048 × 2048 的透明 PNG，所有图层严格对齐在同一张画布上**（就像 PSD 的图层原样导出，不要裁切、不要挪位置）。`rig_studio` 会自己裁出有内容的区域。
- 角色：**三分之四侧身站姿，面朝画面左侧**（近侧手臂、前腿在画面左边），双脚着地。
- 原点：角色中线 `x = 1024 px`，**脚底所在的地面 `y = 1984 px`**；头顶大约在 `y = 180 px`，身高约 1800 px。
- 换算到骨骼空间（运行时用）：`x_rig = 128 + (x_px − 1024) × 0.125`，`y_rig = 256 + (y_px − 1984) × 0.125`。即中线 x=128、地面 y=256，和大肥鱼相同，动作参数可以直接复用。
- sRGB，8 位 RGBA，**不要预乘 alpha**；边缘干净，不要有半透明的白边或杂点。
- **被遮挡的部分要补画完整**：手臂后面的衣服、刘海后面的额头和脸、裙子里面的腿根、身体后面的尾巴根。动起来时这些地方会露出来。
- 风格：Q 版（约 2.5–3 头身），线条和配色适合 300 px 左右的显示高度；二创原创设计，不用任何公司的官方 logo（围裙等处也不要放商标）。

## 3. 图层清单（文件名固定）

身体图层放 `art/<角色>/layers/`，**文件名就是部件 id**。z 越大越靠前；表里的 z 是默认值，`rig_studio` 里可以微调。

| 文件 | 内容 | 跟随的变形器 | 默认 z | 必需 |
| --- | --- | --- | --- | --- |
| `hair_back.png` | 后发（披在身后的长发） | `hairSway` | 1.0 | ✓ |
| `hair_back_curl.png` | 后发卷 / 发尾 | `hairSway` | 1.1 | |
| `fin_far.png` | 远侧耳鳍（白龙：远侧龙角；Claude 少女：远侧发饰） | `finFar` | 1.5 | ✓ |
| `tail.png` | 尾巴（鲸尾 / 龙尾；Claude 少女可以是书包挂件或留空） | `tailBend`（根部在**右端**） | 2.0 | |
| `leg_back.png` | 后腿（从裙子里的腿根画起） | `legBack` | 2.5 | ✓ |
| `arm_far.png` | 远侧手臂（含挡在身前的手） | `armFar` | 3.0 | ✓ |
| `leg_front.png` | 前腿 | `legFront` | 3.4 | ✓ |
| `body.png` | 上身（躯干、领口、袖口以内） | `waist` | 4.0 | ✓ |
| `skirt.png` | 站姿裙子 | `skirt` | 4.5 | ✓ |
| `skirt_shadow.png` | 裙子投在腿上的阴影（会用正片叠底） | `skirt` | 4.6 | |
| `waist_bow.png` | 腰部蝴蝶结 / 腰饰 | `waist` | 4.8 | |
| `skirt_sit.png` | 坐姿裙子（坐下时替换站姿裙子） | `skirtSit` | 4.5 | |
| `face.png` | 脸（**不含五官**），含耳朵 | `headMid` | 7.0 | ✓ |
| `sidelocks.png` | 左右鬓发（可合在一张） | `headFront` | 8.6 | ✓ |
| `bangs.png` | 刘海 | `bangsSway` | 9.0 | ✓ |
| `fin_near.png` | 近侧耳鳍 / 龙角 / 发饰 | `finNear` | 9.2 | ✓ |
| `headdress.png` | 头饰（女仆头饰、发箍） | `headFront` | 9.4 | |
| `bow.png` | 头上的蝴蝶结 | `headFront` | 9.5 | |
| `ahoge.png` | 呆毛 | `ahoge` | 9.6 | |
| `arm_near.png` | 近侧手臂（在身体前面） | `armNear` | 10.0 | ✓ |

变形器名字是动作引擎写死的（`body`、`waist`、`neck`、`armNear`、`armFar`、`legBack`、`legFront`、`tail`、`tailBend`、`skirt`、`skirtSit`、`hairSway`、`bangsSway`、`headBack`、`headMid`、`headFeat`、`headFront`、`finNear`、`finFar`、`ahoge`），不新增、不改名；某个部件不存在就不出那张图。

## 4. 五官与表情（文件名固定）

放 `art/<角色>/layers/face/`，同样是 2048 全画布对齐，位置和 `face.png` 上的五官位置一致。

**中性眼分四层**（视线跟随鼠标时虹膜和高光会移动）：

```text
face/neutral/eyeL_ball.png   eyeL_iris.png   eyeL_hl.png   eyeL_lash.png
face/neutral/eyeR_ball.png   eyeR_iris.png   eyeR_hl.png   eyeR_lash.png
face/neutral/mouth.png       face/neutral/brows.png（可选）
```

`eyeL` 指**画面左边**的眼睛（近侧眼）。虹膜层要比眼眶略大一圈地画完整（移动时不露白边，眼白层会把多余部分遮住——眼白层就是眼眶形状）。

**其他表情每种一组**（整块贴片：一只眼一张，嘴一张，眉可选）：

```text
face/<表情>/eyeL.png  eyeR.png  mouth.png  brows.png（可选）
```

| 表情目录 | 含义 | 用在 | 必需 |
| --- | --- | --- | --- |
| `neutral` | 平静（上面的四层眼） | 待机、说话、思考、干活 | ✓ |
| `happy` | 开心（弯眼、张嘴笑） | 庆祝、台词【开心】 | ✓ |
| `love` | 喜欢（星星眼或爱心眼） | 被摸头、台词【喜欢】 | ✓ |
| `surprised` | 惊讶（圆眼、小 o 嘴） | 被戳、台词【惊讶】 | ✓ |
| `sleep` | 闭眼（**也用于眨眼**，必须和中性眼同位置） | 犯困、眨眼、换表情时的过渡 | ✓ |
| `drag` | 慌张（>< 眼、波浪嘴） | 被拎起、下落、台词【慌张】 | ✓ |
| `dizzy` | 晕（蚊香眼） | 摔晕、出错 | ✓ |
| `shy` | 害羞（眼神躲闪、抿嘴；腮红由程序画） | 台词【害羞】 | |
| `smug` | 得意（半眯眼、嘴角上扬） | 台词【得意】 | |

说话时张开的嘴：`face/talk/mouth_open.png`（可选；没有就把 `happy/mouth.png` 纵向拉伸代替）。

腮红颜色、肤色写在 `pack.json` 里（见下），不用出图。

## 5. `pack.json`（源素材清单）

T5 交图时给出初稿（至少 `name`、`credits`、`palette`），支点可以先空着，由 `rig_studio` 标注后写回。坐标全部用 **画布像素**。

```json
{
  "format": "AICompanionPackSource",
  "version": 1,
  "id": "deepseek-whale",
  "name": "鲸娘",
  "canvas": [2048, 2048],
  "origin": {"center_x": 1024, "ground_y": 1984},
  "credits": [{"role": "原画", "by": "…", "tool": "…", "license": "…", "notes": "生成工具与提示词、人工修图说明"}],
  "palette": {"blush": [255, 120, 140], "skin": [255, 232, 220], "accent": "#4D6BFE"},
  "pivots": {
    "body": [1024, 1700], "waist": [1024, 1300], "neck": [1010, 900],
    "armNear": [880, 1020], "armFar": [1150, 1020],
    "legBack": [1080, 1500], "legFront": [960, 1500],
    "tail": [1240, 1450],
    "finNear": [820, 600], "finFar": [1230, 600], "ahoge": [1000, 220]
  },
  "head": [600, 160, 1440, 960],
  "parts": {"bangs": {"z": 9.0, "grid": [6, 4]}}
}
```

- `pivots`：各旋转变形器的支点（关节位置）。上面的数字只是示意，以实际图为准。
- `head`：头部视差变形器作用的矩形（覆盖整个头和头发，到脖子为止）。不填时按头部图层的范围自动算。
- `parts`：只写需要覆盖默认值的部件（z、网格密度 `grid`、`blend`）。默认网格 4×4，头发和裙子建议 6×4 以上。

## 6. 版权与提交

- 自己的形象**可以随本项目公开**：`art/<角色>/` 和编译出的 `characters/<角色>/rig/` 都提交。
- `credits` 必须写清出图方式（工具、提示词、人工修改），以及授权；生成过程中不得参考或描摹 Coopanion 的大肥鱼贴图和任何官方形象。
- 单张源图尽量控制在 1 MB 以内（透明区域多，PNG 压缩后通常很小）；如果整个角色超过 30 MB，先在 PR 里提出来讨论是否用 Git LFS。

## 7. T6 代码要做的事（Claude）

1. `tools/rig_studio.py`
   - `compile <art目录> <输出目录>`：读 `pack.json` 和图层，按 alpha 裁剪每个图层（留 4 px 边），算出 `box`，像素坐标换算到骨骼空间，生成 `rig.json`（`source.kind: "pack"`）和 `textures/`；检查必需图层、表情是否齐全、支点是否落在对应图层附近、尺寸和对齐是否一致，问题逐条报出来。
   - `studio <art目录>`：PySide6 小窗口，显示合成后的角色，拖动点标注支点和头部矩形，旁边实时预览（复用 `RigAvatar`，可切姿势、表情、拎起），保存写回 `pack.json` 并重新编译。
2. 角色包接入：`character.json` 的 `renderer.rig` 支持 `{"source": "pack", "path": "rig/rig.json"}`；有自己的形象包时优先用它，没有时才找本机 Coopanion，再不行退回整张立绘。改动集中在 `body/rig_view.py`、`body/rig_import.py`（`load_rig` 校验）和角色包注册处；`ui/main_window.py` 如需改动只动形象选择那一处，会在 PR 里写清楚。
3. `rig.js` 增补：支持 `shy`、`smug` 表情贴片和 `talk_mouth`（没有时保持现在的替代做法）。
4. 占位素材：`tools/make_placeholder_pack.py` 用简单几何图形按本规格生成一套合成占位图层（编进测试，不当正式形象），用来验证编译和渲染，不等 T5 的美术。

## 8. 验收

- 单元测试（不依赖 Qt 的部分）：占位素材 → `compile` → `load_rig` 通过；缺必需图层、画布尺寸不对、图层没对齐、支点离谱时报出明确错误；坐标换算正确（中线和地面落在 128 / 256）。
- 桌宠自检新增：用占位形象包启动骨骼形象，`ready`、透明、命中遮罩生效、7 个以上姿势和全部必需表情能切换。
- 真机：用户在桌面上看占位形象（以及 T5 的第一个正式形象）走、甩、摸、换表情；鲸娘正式形象达到 P1 的验收标准（动作自然；空闲 CPU ≤ 3%）。
- 仓库里没有任何 Coopanion 素材或由它导出的文件。

## 9. 不做

- 不改动作引擎的变形器名字和动作逻辑（形象之间通用）。
- 不提交 Coopanion 的贴图、`rig_import.py` 的缓存产物。
- P7 相关一律不碰。
