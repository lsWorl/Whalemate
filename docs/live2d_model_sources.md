# 导入外部 Live2D 模型

默认角色不使用 Live2D。只有想换成自己的 Cubism 模型时才需要看这份说明。
2026-11-12 起删除了艾米莉娅和 Haru 示例的旧记录，需要时可在提交 `7ebc39c` 及之前的历史中查看。

## 先准备 Live2D Cubism Core

`live2dcubismcore.min.js` 适用 Live2D Proprietary Software License，本项目的仓库和打包版本都不包含它。

- **源码运行**：在项目目录执行 `.venv\Scripts\python.exe tools\fetch_avatar.py`，从 Live2D 官网下载 Cubism SDK for Web 并取出 Core，放到 `src/aicompanion/assets/live2d/vendor/`。这个文件已列入 `.gitignore`，不会被提交。
- **打包版本**：从 Live2D 官网下载 Cubism SDK for Web，把 `Core/live2dcubismcore.min.js` 放到程序目录的 `_internal/aicompanion/assets/live2d/vendor/`。
- 缺少 Core 时，设置里导入模型会直接提示获取方法；已经选了外部模型的角色启动时退回自带形象，并在状态栏提示一次。
- `tools/build_windows.py` 默认从构建结果里删掉 Core。`--keep-cubism-core` 只供自己电脑使用：能导入任意模型的应用带着 Core 对外分发，可能属于 Live2D 的[可扩展应用](https://www.live2d.com/en/sdk/license/expandable/)，需要先经 Live2D 审核并签订专门协议。

## 导入步骤

1. 保留完整的模型文件夹，至少包括 `.model3.json`、它引用的 `.moc3` 和 PNG 纹理。
2. 打开设置里的“桌面形象”，选择“导入 Live2D 模型”，选中 `.model3.json`。
3. 程序会先检查包内文件、模型版本和引用路径，再检查 Core，最后实际加载。
4. 观察桌面上的显示效果，确认眼睛、嘴型、表情和待机动作。

## 兼容性

- 只支持 Cubism 3/4 的 `.model3.json`。旧版 `model.json`、PNG、PSD、`.cmo3` 都不能直接使用，需要先在 Cubism Editor 里导出运行包。
- 模型资源必须用包内相对路径，不能通过 URL、绝对路径或 `..` 读取模型目录以外的文件。
- 口型和表情按模型里实际存在的参数驱动。模型用专用参数名时，需要单独做映射和实测，不能只凭文件后缀保证效果。
- 请只导入你有权使用的模型，并遵守模型作者的使用条款。
