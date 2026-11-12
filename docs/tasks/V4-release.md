# V4-发布：解压即用的 Windows 压缩包、卸载程序、GitHub Releases、README

负责人 2026-11-12 要求：把项目打包成可直接运行的 exe；文件夹里附带卸载程序，能清除应用的全部内容和数据库；上传到 GitHub 供下载；再更新 README。
分工：**Codex 实现和在 Windows 上打包，Claude 审计、上传 Release、更新 README**。分支 `task/v4-release`，从 `collaboration-baseline` 拉出。

## 负责人已确认

- **形式**：压缩包，解压即用。发布包内的文件和目录均采用英文命名。zip 里只有一个 `Whalemate` 文件夹，里面有 `AICompanion.exe`（双击运行）和 `UninstallWhalemate.exe`。不写安装程序，不需要管理员权限。
- **发布**：仓库目前是私有的，先发布到私有仓库的 Releases，负责人自己下载试用；仓库是否公开由负责人之后在 GitHub 上自行决定。

## A. 卸载程序 `UninstallWhalemate.exe`

新建 `src/aicompanion/uninstall.py`（入口 `tools/uninstall_entry.py` 或 `python -m aicompanion.uninstall`），用 PyInstaller 单独打成 `UninstallWhalemate.exe`，放在 `AICompanion.exe` 旁边，图标沿用应用图标。界面用 V4-UI 的鲸鲸主题和控件规范。

### 1. 先列清单，确认后才删
打开后先扫描，列出**将要删除的每一项**：路径、大小、说明。清单至少覆盖：

| 内容 | 位置（以代码里的实际逻辑为准，逐个核对） |
| --- | --- |
| 程序本身 | 卸载程序所在文件夹内的发布清单文件及已知运行时产物，包括本机安装的 Aboutme 动画（`_internal/aicompanion/characters/*/animation-*`）、Cubism Core（如果有）、`.env` |
| 聊天、记忆数据库 | `.aicompanion-paths.json` 记录的记忆目录里的 `aicompanion.sqlite3`，以及 `*.backup-*.sqlite3`、`-wal` / `-shm`；默认位置是 `D:\AICompanion\data` 或其他非系统盘；还有旧版的 `%LOCALAPPDATA%\AICompanion\data` |
| 路径记录 | `.aicompanion-paths.json`，以及 `AICOMPANION_PATHS_FILE` 指定的文件 |
| 表情图库 | 默认图库目录（由程序创建的）；用户自己选的外部图库见下 |
| 模型密钥 | DPAPI 加密保存的凭据在数据库里就随库删除；如果还有存在 Windows 凭据管理器或其他文件里的，一并清除 |
| QQ 组件 | `%LOCALAPPDATA%\AICompanion\qq\`（NapCat、AstrBot、uv 运行环境）；删除前先停掉还在运行的 AstrBot / NapCat 进程 |
| 其他本机数据 | `%LOCALAPPDATA%\AICompanion\` 下的语音缓存、本地语音识别模型（`models\`）、角色转换缓存（`rig_import`）等；`%USERPROFILE%\AppData\LocalLow\AICompanion\`（办事沙箱、DSH 会话）；日志 |
| 开机启动 | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下本应用的那一项 |

请用 `grep LOCALAPPDATA / LocalLow / APPDATA / Run` 等方式把代码里写数据的所有位置找全，写进 `docs/storage-locations.md` 的“卸载会删除哪些内容”一节；**清单和这份文档必须一致**，并配一个测试保证一致。

### 2. 用户自己选的外部目录
如果记忆目录或表情图库是用户在“存储位置”里指定的、不在默认位置：
- 记忆目录里只删本应用的数据库文件，以及本应用在里面创建的子目录和记录，不删这个目录里的其他文件；
- 外部表情图库默认**不勾选**删除，在清单里单独一行，带复选框“同时删除我的表情图库（里面可能有你自己收集的图片）”。

办事回收区和旧版快照默认保留，有独立的复选框，明确提示删了就找不回。发布时生成 `_internal/whalemate-files.json`，卸载程序内嵌同一清单用于核对。

### 3. 安全底线（每条都要有测试）
- 只删除清单里列出的路径。任何一项如果解析到磁盘根目录、用户主目录、系统目录，或者“程序文件夹”看起来不是鲸伴的程序目录（判断依据：旁边有 `AICompanion.exe` 和 `_internal`），就拒绝并报错，不删任何东西。
- 鲸伴正在运行时，先提示退出（或者代为正常退出），等它退出后再删；QQ 子进程同样处理。
- 确认步骤：最后一步要求勾选“我知道删除后无法恢复”，再点“卸载”。
- 删除时显示进度；某一项失败（被占用、没有权限）不能中断其他项，结束时列出成功和失败的项，并告诉用户失败项怎么手动删。
- 程序文件夹最后删：卸载程序退出后，由一个临时的延迟删除脚本或进程删除 `UninstallWhalemate.exe` 本身，并在程序目录为空时删除该目录。个人已知目录即使为空也保留，程序目录里的非应用文件在清单中标为保留。
- 提供 `--dry-run`：只输出清单，不删除，供测试和用户预览。

## B. 打包脚本

改 `tools/build_windows.py`，或者新增 `tools/build_release.py`：

- 在**全新的临时目录**里构建，绝不复用本机 `dist\AICompanion`，确保不会带进 `.env`、`data\`、数据库、日志、Aboutme 贴图、Cubism Core、`__pycache__`。打包结束后扫描产物，发现这类文件就直接失败。
- 产物：`dist/release/Whalemate-v<version>-windows-x64.zip`，版本号取 `pyproject.toml`（当前是 0.1.0）。zip 里只有顶层的 `Whalemate/` 文件夹，内含 `AICompanion.exe`、`UninstallWhalemate.exe`、`_internal/`、`LICENSE`、`README.md`、`README.en.md`、`docs/`（aboutme-pet、live2d_model_sources、storage-locations）、`tools/`（Aboutme 安装器）、`.env.example`。
- 同时生成 `SHA256SUMS.txt`。
- 两个 exe 都在 `--windowed` 模式下设置图标和版本信息（产品名为 `Whalemate`，文件说明分别为 `Whalemate`、`Whalemate Uninstaller`）。

## C. 验证（Windows）

1. 照常运行 `tools\run_checks.bat`。
2. 在一个全新的临时目录里解压 zip，依次运行 `AICompanion.exe --smoke-test` 和 `--pet-smoke-test`。
3. 用临时的 `LOCALAPPDATA`、`USERPROFILE` 和路径记录模拟一台“用过一段时间”的电脑：有数据库、备份、默认图库和外部图库、QQ 组件目录、LocalLow、开机启动项，然后：
   - `UninstallWhalemate.exe --dry-run`：清单完整、一致；
   - 真正卸载：清单里的内容全部删除，外部图库默认保留，其他无关文件和目录毫发无损；
   - 再分别测：鲸伴正在运行时卸载、某个文件被占用时卸载。
   **不要在负责人真实的数据上测试。**
4. 把 zip 的完整路径、大小、SHA256 和测试结果写进 PR。

## D. README（Claude 负责）

中英文 README 新增“下载安装”和“卸载”两节：去 Releases 下载 zip → 解压到非系统盘（例如解压到 `D:\Apps` 后打开 `D:\Apps\Whalemate`）→ 双击 `AICompanion.exe` → 跟着向导走；卸载就运行 `UninstallWhalemate.exe`，并说明它会删除哪些内容、外部图库默认保留。原来的“快速开始”改为“从源码运行（开发者）”。

## 不做

- 不做安装程序，不写系统级注册表（开机启动项除外，那是用户自己开的）。
- 不改业务逻辑和数据库表结构。
- 不在负责人真实的数据上测试卸载。

## 可选的本机 QQ 接入包

本机留存的 `NapCat.Shell.Windows.Node.zip` 如与 v4.18.41 官方哈希一致，可在构建时直接附带。将 `--napcat-zip` 指向原版 ZIP，`--napcat-repo` 指向保有固定提交的 NapCatQQ 本地仓库。脚本只从仓库读取该提交的 LICENSE。两个参数同时指定时，`tools/build_release.py` 输出到 `dist/local-napcat/`。常规发布仍输出到 `dist/release/`。

本机包解压后的 `Whalemate/third-party/napcat/` 包含未改动的原版 ZIP、完整 LICENSE 和 NOTICE.txt。NapCat 仅限非商业用途。NapCat、腾讯 QQ 二进制和 Node.js 均适用各自的许可。面向其他用户分发 QQ 运行时的授权尚未核实。

**2026-11-12 负责人决定：** v0.1.0 起 Releases 压缩包随附这份未改动的 NapCat 官方 Windows 包（含其中的腾讯 QQ 运行时文件），连同完整 LICENSE 和 NOTICE。腾讯运行时的再分发授权仍未找到书面依据；该决定由负责人作出并承担，仅限非商业用途。如上游或腾讯提出异议，撤下 Releases 中的附带包，改回由用户在“QQ → 高级设置 → 本地 NapCat 包”里指定原版 ZIP。

用 `tools/verify_release.py <ZIP> --case qq` 运行联网的扫码前验收。它启动解压出的主程序，在临时用户目录里准备真实组件，检查 WebUI、二维码、AstrBot 监听和插件心跳，随后停止服务。正式账号登录由负责人扫码。[构建来源与实测记录](V4-qq-local-napcat.md)
