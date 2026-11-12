# 更改记忆和表情包的保存磁盘

更新到本功能后，打开 **设置 → 存储位置**，分别选择：

- **记忆和聊天目录**，例如 `D:\AICompanion\memory`。聊天、记忆、心事、偏好和表达习惯在同一个 SQLite 文件中，会一起保留。
- **表情包目录**，例如 `E:\AICompanion\memes`。可使用已有的分类图库，或选择一个新空目录；新目录会补入仓库自带原创鲸娘表情。

点击窗口底部“保存”，再从托盘彻底退出并重新打开桌宠。记忆在下次正式启动、旧实例退出之后复制，包含保存设置后到退出前的新消息；不会复制正在变化的裸数据库文件，也不会删掉旧库。检查新目录正常使用后，旧文件仍由你自行管理。

目标目录若已有同名数据库，程序拒绝覆盖，请选择新的空目录。磁盘离线、空间 / 权限不足、原库损坏或路径不一致时，不会悄悄创建空库代替记忆；旧库保留并报告启动失败。请保留旧目录及路径记录，修复磁盘问题后重试。

开发版默认当前项目盘的 `data/` 和 `data/memes/`。例如本项目在 `D:\Study\AICompanion`，默认就是：

```text
D:\Study\AICompanion\data\aicompanion.sqlite3
D:\Study\AICompanion\data\memes\
```

程序安装在系统盘时，默认优先 `D:\AICompanion\data`，其次其他可用的非系统盘，不回退到 C 盘保存记忆。旧版 AppData 数据库或旧系统盘路径仍存在时，下次正式启动会通过相同安全复制流程保留其中数据，旧库不删除。

路径记录是 `.aicompanion-paths.json`：开发版在项目旁；程序位于系统盘时放在非系统盘的 AICompanion 目录。里面只有目录和迁移状态，不含 API 密钥，已加入 Git 忽略。高级环境可通过 `AICOMPANION_PATHS_FILE` 指定这个小文件的位置。用户在页面选定的路径优先于旧 `DATABASE_PATH`，不需要编辑 `.env`。

表情图库可从本页或 T12“聊天平台”页选择，两处不会用旧值相互覆盖。已有图库不被覆盖或删除；只更换使用目录，不会自动搬走或复制外部图库。选定的新表情目录在保存后用于聊天，记忆库路径则需重启生效。

此页控制聊天 / 记忆库及桌宠表情图库；QQ 组件安装位置是聊天平台里的独立设置。


新建空表情目录会补入六组带中文含义的原创图片：开心庆祝、思考疑惑、困了晚安、惊讶被戳、无语出错、耐心等待。不会生成限制分类的 manifest.json，之后可以继续添加自己的分类目录。已有图库（包括旧英文分类）不会自动改名或覆盖。

当前路径记录损坏会停止启动；错误定位信息待后续完善。可移动磁盘失联 / 盘符复用后的识别仍未完善，现阶段建议固定本地磁盘，不把未找到原库时的新空库当作成功迁移。迁移完成的标记保留作审计，不包含聊天正文。

## 卸载会删除哪些内容

解压后运行 `Whalemate/UninstallWhalemate.exe`，程序会逐项列出绝对路径、字节数和用途。勾选“我知道删除后无法恢复”并点击“卸载”后才开始。运行 `UninstallWhalemate.exe --dry-run` 可预览同一清单，重定向输出时使用 UTF-8。卸载窗口由临时副本运行，因此可以在原程序退出后删除安装目录，并显示实际成功或失败的各项。

| 清单用途 | 查找位置和范围 |
| --- | --- |
| 程序、运行配置与本机安装的角色资源 | 解压得到的整个 `Whalemate/` 目录，包括两个 exe、`_internal`、本机安装的 `animation-*`、Cubism Core、安装器缓存和 .env。其他数据先删，程序目录最后删。 |
| 记忆数据库、备份、迁移记录和数据库内的 DPAPI 凭据 | 当前和默认记忆目录、未完成迁移的源和目标，以及旧版 LocalAppData 数据目录。按本库文件名查找 .sqlite3（或已记录的 .db、.sqlite）、同名前缀的 .backup-*.sqlite3、-wal、-shm、-journal 和 .migration-<编号>.json。也识别旧 DATABASE_PATH。默认数据目录清空后移除，外部记忆目录及其中其他文件保留。模型密钥随库删除。 |
| 存储位置记录 | 程序旁、默认数据目录旁的 .aicompanion-paths.json，以及 AICOMPANION_PATHS_FILE 指向的有效记录。 |
| 默认表情图库 | 默认数据目录下的 memes 及其中的分类和图片。旧路径记录只指定记忆目录时，也会识别该目录下自动使用的 memes。 |
| 用户选择的外部表情图库 | 从路径记录及数据库 qq_meme_root 读取。默认逐项标为保留，另有“同时删除我的表情图库”复选框。图库在其他待删目录内时也会保留其内容和必要的上级目录。 |
| 旧数据库、语音缓存、识别模型、角色缓存和日志 | %LOCALAPPDATA%\AICompanion 下的全部应用文件，包括 data、voice-cache、models、rigs 和日志。 |
| 办事沙箱和 DSH 会话 | %USERPROFILE%\AppData\LocalLow\AICompanion 整个目录，含 dsh 和沙箱临时文件。 |
| QQ 组件和运行环境 | %LOCALAPPDATA%\AICompanion\qq。高级设置 qq_root 指向外部目录时，按安装记录识别 manifest.json、downloads、napcat、uv、astrbot、python、uv-cache、astrbot-home、napcat-home、qq-data、logs。停止时核对组件的进程路径、PID 和创建时间。 |
| 办事回收区和旧版快照 | 各盘根目录的 .aicompanion-trash、.aicompanion-snapshots；已配置博客目录的 .aicompanion-trash；LocalAppData 应用目录内的 trash。 |
| 当前用户的鲸伴开机启动项 | HKCU\Software\Microsoft\Windows\CurrentVersion\Run 中名为 AICompanion、指向本次安装的值。 |

正在使用鲸伴时请先从托盘退出，点击卸载窗口的“重新扫描”，核对退出后写入的文件并再次确认。程序目录须包含 AICompanion.exe、UninstallWhalemate.exe 和 _internal。遇到磁盘根、用户主目录、系统目录、链接或联接点会拒绝操作。文件被占用时会显示具体路径，可关闭占用程序后手动清理。预览后新增的文件会留在原处。
