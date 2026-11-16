# T27 桌宠移动时出现黑色边框

负责人 2026-11-16 反馈：有时把桌宠移到桌面上，会出现黑色边框。Claude 排查，Codex 审查。

## 现状（代码阅读，尚未复现）

- 桌宠窗口是 `Tool | FramelessWindowHint | WindowStaysOnTopHint | NoDropShadowWindowHint`，设置了 `WA_TranslucentBackground`。构造时调用 `winId()`，先建好带 alpha 的原生表面。
- 视频逐帧形象（`spritesheet`）不设窗口遮罩（`_update_mask` 直接 `clearMask()`）。整窗是透明背景，绘制时先用 `CompositionMode_Source` 填透明，再画当前帧。
- 鼠标在角色身上和离开时，`_set_input_transparent` 会切换 `WindowTransparentForInput`。在 Windows 上，这一步会重新设置原生窗口的扩展样式。

## 怀疑方向（按可能性排序，需要复现确认）

1. **切换鼠标穿透标志时，分层窗口属性短暂丢失。** 如果 `WS_EX_LAYERED` 在重设扩展样式时被清掉，透明区域会在下一次刷新前显示成黑色。这与“有时”“移动时”吻合：拖动和悬停正好频繁切换这个标志。
2. **跨显示器或缩放比例变化。** 拖到另一块缩放比例不同的屏幕时，Qt 会重建表面或调整尺寸；如果新的表面在第一次绘制前被合成，会出现一圈黑边。
3. **窗口尺寸变化后的第一帧。** 改大小、出现气泡时，如果新区域在绘制前就显示出来，也会露出黑底。

## 需要负责人提供

- 出现黑边时的截图：是整个窗口一圈矩形，还是贴着角色轮廓的一圈。
- 是否多显示器、各屏缩放比例，以及是否刚好从一块屏拖到另一块。
- 黑边持续多久：一闪而过，还是要再动一下才消失。

## 计划

1. 在 Windows 上用 `tools/smoke_pet.py --preview` 反复悬停、拖动、跨屏，截屏复现；同时读取窗口扩展样式，确认 `WS_EX_LAYERED` 是否在切换时消失。
2. 按复现结果修复。候选做法：
   - 鼠标穿透改用 `WM_NCHITTEST` 返回 `HTTRANSPARENT`，不再切换窗口标志。
   - 或者在切换后强制重绘一次。
   - 或者在屏幕变化（`screenChanged`）时先隐藏，绘制完成后再显示。
3. 补自检项：切换穿透和跨屏后，窗口四角像素的 alpha 保持为 0。

## 负责人补充（2026-11-16）

黑边是**整个窗口一圈方框**，**一闪就没**。这符合怀疑方向 1：重设窗口样式时，分层窗口重新上屏前露出一帧黑底。跨屏重建表面通常会持续到下一次绘制，不像一闪即逝。

## 修复

- `desktop_integration.set_click_through(widget, enabled)`：在 Windows 上直接读写原生窗口的扩展样式，只切换 `WS_EX_TRANSPARENT` 一位，保留 `WS_EX_LAYERED`、置顶等其他位。样式已经是目标状态时不写。
- 窗口不是分层窗口，或者不是 Windows 时，返回 False，仍走原来的 Qt `setFlag`，不改变其他平台的行为。
- `DesktopPetWindow._set_input_transparent` 优先用它。每次都对照实际样式，所以 Qt 因为置顶等标志变化重设样式后，下一次检查就能补回来。

## 验证

- `tests/test_click_through.py`（用假的 user32）覆盖：
  - 分层窗口上只切换透明这一位，其他位保留。
  - 已是目标状态时不写。
  - 非分层窗口和非 Windows 平台交回 Qt。
- 待 Windows 实测：
  - 确认桌宠窗口的扩展样式带 `WS_EX_LAYERED`，即确实走新路径。
  - 鼠标在角色上时能点中，离开后能穿透。
  - 反复悬停、拖动时不再闪黑框。
