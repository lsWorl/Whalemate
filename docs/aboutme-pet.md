# Aboutme 大肥鱼本机素材

用户指定来源：[hunmer/aboutme-pets](https://github.com/hunmer/aboutme-pets)，固定提交
1221697ec20c2057829f9d7fcbd316fee8b3df65。资源来自 dsh / dsh1，不读取本机 Coopanion。

## 安装

在本任务工作树运行（只需首次联网下载）：

    .venv\Scripts\python.exe -m pip install -r tools\aboutme-requirements.txt
    .venv\Scripts\python.exe tools\install_aboutme_pet.py

离线重装可加 --offline；需要已缓存的全部源 WebP。锁定清单
tools/aboutme-pet.lock.json 记录源路径、尺寸、帧率、字节数和 SHA-256；
下载后校验，缓存损坏直接报错。应用启动不联网、不自动下载。
未安装时退回基线立绘。包、缓存、预览和回收区都被 Git 忽略，
公开 PR 只含接入代码与文字记录，不包含第三方贴图。

## 归属与使用范围

请阅读上游 [README](https://github.com/hunmer/aboutme-pets#readme)、
[NOTICE](https://github.com/hunmer/aboutme-pets/blob/1221697ec20c2057829f9d7fcbd316fee8b3df65/pets/dsh1/NOTICE.txt)
以及原始来源 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。
上游注明 dsh 素材非商用，公开衍生作品需保留署名；仓库代码许可不能当作贴图许可。
本接入不扩展这些权利，也不将素材重新授权。DeepSeek 角色属于其相应权利人；
Aboutme 的整理和动作资源、上游作者的贡献均应保留归属。

## 播放方式

21 个源动作映射到待机、说话、思考、忙碌、等待、庆祝、睡眠、拖拽、
挥手、害羞、生气、小跳、笑、屈膝礼、好奇、伸懒腰、摇摆、奶茶、得意和行走。
菜单显示源动作名称，不把摇摆宣称为新绘制的抱住自己动作。

整帧统一从 640×360 等比缩至 512×288，保持全画布与 12 FPS，
无光流、分肢、逐帧包围盒对齐或重绘。较长的 180 帧也维持图集 32M 像素上限。
行走只取源 moving 标注的第 15～94 帧（从零计数）；右行走整帧镜像左行走，
仅支持左右路线。距离推进按上游片段时长换算，每秒 40 个本机画布像素，
不再把路线长度强行对齐猜测的半步周期。到达后使用现有短淡入衔接待机。
源动画本身的质量与循环接缝不因打包而被保证。

此前本任务的自绘与专用生成脚本已移入 output/recycle/aboutme-migration-2026-11-11，
receipt.json 列出原路径；保留用户参考图、T5/T6 基线与协作者文件。
