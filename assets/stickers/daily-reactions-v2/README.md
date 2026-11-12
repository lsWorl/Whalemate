# 大肥鱼日常反应图库 v2

共 171 张：保留原 80 张鲸娘 GIF，补 29 张不同用途的鲸娘 GIF，以及 62 张通用反应 PNG。不是将 171 张塞进提示词；每轮仍由现有大脑在最多 10 个候选中自主选择 0～1 张。没有关键词触发或固定发送比例。

## 覆盖与边界

明确补充祝贺 6、慰问 / 陪伴 5、震惊 5、困惑 5、晚安 5 张通用图，另有新增鲸娘惊讶和敲鼓庆祝；还覆盖偷笑、尴尬、无语、收到、感谢、道歉、问候 / 告别、鼓励、亲昵、委屈、难过、抗议、保密、吃瓜、饮食、游戏、欣赏、得意、恢复状态等日常用途。图片情绪与表达意图分别标注，不把“他难过”自动变成“她哭泣”。

亲密表情单独标 owner_family，不能默认给普通群友。安慰中的普通关心图与抱抱 / 爱心图分开。严肃、悲伤、冲突仍应由大脑谨慎判断，允许不用图片；不为凑数量强配图。

原图库 / 标注不覆盖。本清单保留原 80 张及 stable ID；再次导入时现有审核和用户标注遵循 AssetCatalog 的保留规则。新增图使用独立 provider / 原标识 / SHA，source、author、license、license_evidence、reviewed_at 都逐项可查。

## 许可必须随包保留

许可正文完整保留；仓库副本仅规范换行和行尾空白。

- 109 张鲸娘：作者“可以叫我云大人 / 雾雪”，https://space.bilibili.com/103739 ，https://kiriyuki.com 。GIF 按作者公开的非商业署名分享条件使用，版权归作者；知识标注另为 CC BY-NC 4.0。不可将代码 Apache / MIT 当成图形许可。基础形象另保留 ZipZipPipe / 上善无形的来源说明，详见 licenses/WHALE-ASSETS.txt。
- 62 张通用反应：Twemoji / Twitter, Inc. 及其他贡献者，维护仓库 https://github.com/jdecked/twemoji ，图形许可 CC BY 4.0，https://creativecommons.org/licenses/by/4.0/ 。保留署名、仓库和许可链接；此处用 Qt 将原 SVG 渲染为 256×256 透明 PNG，没有重绘或加字。原始 SVG 和版本 / 源 SHA 一并保留，详见 licenses/TWEMOJI-CC-BY-4.0.txt。
- 鲸娘标注来源固定为 Loping151/dsh-nekowhale-stickers@b7b58c0ff347d639fabe87d90d6716c4b3cad185；Twemoji 固定为 b6b55fef1e8636b540a6d016a4729ca8cdf2e60b。每项 GIF 从作者公开网址下载，未读取 Coopanion、其导出或衍生图片。
- 其他库即使代码开源，也没有因此取得图片授权；本包不混入权利不明来源。

## 准备与导入

源码里包含完整目录、62 张 PNG、对应原 SVG 与许可。109 张 GIF 通过固定 URL / SHA 获取，不把约数十 MB 的第三方二进制再次塞入 Git。

在项目根目录执行（输出请选择非系统盘，下面只是示例）：

```powershell
.\.venv\Scripts\python.exe tools\prepare_daily_stickers.py --out D:\AICompanion-Materials\daily-v2
```

已有原 80 张包时可加 `--reuse-manifest 原包的manifest.json`；可重复传入多个清单复用缓存。不重新下载匹配 SHA 的目标文件，不覆盖目标冲突；未知许可、错误 SHA、越界路径会拒绝。工具只校验目录、大小与固定 SHA；正式导入仍由原图库完整解码检查。

运行后，将输出的 manifest.json 交给“设置 → 表情图库 → 导入素材清单”，图库目录仍沿用原 qq_meme_root。本工具不读取或修改应用数据库、账号或存储根配置。下载暂存 / 准备目录只是材料交付位置，不是第二套运行存储系统。

原 GIF 只保留体积 / 帧数 / 累计像素符合现有 1 MiB 协议且已经审核的版本；较大的源图未擅自压缩、截帧或改画。首帧 / 中间帧已人工查看，完整解码 / SHA 校验通过；不是人工逐秒看完每段动画的保证。
