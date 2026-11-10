# T20-动画 Aboutme 大肥鱼画面接入

负责：Codex。独立分支 task/t20-original-whale-animation，PR #25 草稿。
当前实现与本机自动验收通过；待用户真机看效果、另一方最新提交审查及用户合并。

## 当前范围

用户明确要求直接切换 hunmer/aboutme-pets，并授权清理先前不合格自绘。
停止自绘迭代，固定上游提交 1221697ec20c2057829f9d7fcbd316fee8b3df65，
选择 dsh/dsh1 共21个源动作。本机打包成22个逻辑动作（左/右行走共享源图）。
上游完整帧、原12FPS、统一512×288，行走采用 moving 标注15～94帧；
右行走镜像，角色不重画、不拆肢体、不额外插帧。仅支持左右路线。
人格、关系、音色、聊天渠道与Agent不在本轮变更范围。

## 代码与安装

- 新增 tools/install_aboutme_pet.py、tools/aboutme-pet.lock.json 与 docs/aboutme-pet.md，
  固定路径/大小/SHA256；上游贴图及转换包放 Git 忽略的本机目录，不提交公开PR。
- 安装沿用已有 Pillow 12.3.0，锁定 tools/aboutme-requirements.txt，不增加运行依赖。
  先联网下载；--offline 可从校验缓存重装。运行时不联网、不自动下载。
- 角色默认指向 animation-aboutme；缺少本机包时退回基线立绘。
  沿用后台Qt解码、有限预载、可打断淡入、透明穿透、聊天和拖拽。
- 移动距离驱动帧相位，按原片段时长40画布px/s；不猜测半步相位去缩放步态。
  点击别名解析到实际源动作，菜单保留左右入口，右向明确标记镜像。
- 保留通用解析、frameMap、过渡和移动回归，退休只针对失败素材/生成工具的测试。
  新增合成图集完整格顺序/透明、缓存拒绝、离线回退、方向和固定步速测试。
- 不从本机 Coopanion 读取或导出。归属与限制见上游 README/NOTICE 和本接入说明。

## 回收

426个本任务新增的旧图纸/运行包/专用工具与测试，共227,012,097字节；
另回收14个旧预览目录中2,604个文件，共1,087,123,984字节。
均移动到 output/recycle/aboutme-migration-2026-11-11，可恢复，未永久删除。
receipt.json 与 old-previews/receipt.json 分别列出原路径/数量。
用户原始参考图 user-identity-reference.png、T5/T6基线立绘/骨骼及协作者
未跟踪 locomotion-locked-v1 未动；其他任务目录未清理。

## 验证（Windows）

- tools/run_checks.bat 退出0：792项，785通过/7跳过，107.018秒；
  Smoke passed=true、errors=[]、failed_checks=[]、remaining_workers=0。
  实际22动作加载、待机→忙碌→思考→待机淡入、左右60px到达、
  切基线立绘再切回Aboutme及原聊天/拖拽/缩放/透明穿透等检查通过。
- 点击/菜单小修正后针对性72项退出0，2.689秒；Pillow可选测试守卫随后仅改导入边界。
- 临时库+Mock实际窗口录制93帧，84ms间隔：左右各120px到达，
  各推进36个帧索引，using_aboutme=true、errors=[]。
  主进程工作集短时峰318.1MiB/最低235.5MiB，含PNG录制开销；
  不包含启动器，不代表长期内存目标验收。output/aboutme/live-report.json、
  runtime-walking.gif。本机21张WebP共121,907,918字节。
- 未读取.env、真实用户库或私有素材；未联网调用模型、语音或麦克风。
- 未验证：源动画的自然度/循环接缝人工验收、长期内存、多屏/高DPI/低性能设备、
  另一方最新提交代码审查。未合并到D盘基线，不标记“可合并”。
