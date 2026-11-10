# 大肥鱼透明动画素材

来源：[hunmer/aboutme-pets](https://github.com/hunmer/aboutme-pets)，固定提交 `cb205c271517336b500981a2cca050a1759bceaa`。
上游：[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，固定提交 `b78e3fcf92ac78cee569aed7cc27eeb2a8d51757`。

**素材禁止商用，公开二创须署名上述上游仓库。** `LICENSE-*.txt` 的 MIT 仅覆盖上游代码，不解除素材限制。原始 `NOTICE.txt`、`SOURCE-upstream.txt` 和许可文本均原样保留；此目录没有 Coopanion 来源的文件。

首批选用 13 张 WebP 图集，原文件未重编码；`SOURCE.json` 记录来源路径、字节数和 SHA256。帧为 640×360、12 fps、8 列；绝大多数动作 120 帧，垂头叹气动作 59 帧，末行空白格不参与播放。`poster.png` 是待机第一帧，用于后台首次解码前显示。

`animations.json` 将待机、说话、思考、工作、等待、庆祝、犯困、出错、拖拽、被戳、摸头、行走和挥手映射到现有桌宠状态。下落借用拖拽、晕眩借用出错。动画中的嘴部动作来自原片，不提供音素级实时口型；素材自带音轨没有引入。

桌宠右键 → **动画动作** 可单独播放动作；右键 → **形象** 可切回原分层骨骼或原立绘。切换只影响外观。资源损坏时显示错误并退回原立绘。

重新获取固定版本：在项目根目录执行 `.venv\Scripts\python.exe tools\fetch_aboutme_assets.py`。工具不读取账号或 `.env`，不执行远程代码，不覆盖与固定版本不一致的既有图集。
