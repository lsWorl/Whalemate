# V4 · QQ 本地 NapCat

## 2026-11-12 构建调查

基线 `285b0d3`。本机源码镜像位于 `D:\Study\napcat\NapCatQQ`，当前 HEAD 是 `72912f2`。以下均用 `git show v4.18.41:<路径>` 核对标签（提交 `859eb530c5801577633c2c3ae871d23586edf281`），可在不切换原仓库工作区的情况下复核。

| 桌宠所需文件 | 上游发布流程中的来源 |
| --- | --- |
| `napcat/napcat.mjs` | Node 20.x、pnpm 9 下运行 `pnpm i`、`pnpm --filter napcat-webui-frontend run build`、`pnpm run build:shell`、`pnpm --filter napcat-plugin-builtin run build`。暂存 Shell 的 dist，移除暂存清单的 devDependencies，运行 `npm install --omit=dev`。 |
| `index.js` | 标签内的 `packages/napcat-develop/index.js`。同目录还提供 `QQNT.dll`。 |
| `wrapper.node` | 用 7-Zip 解开上游固定的 Windows x64 QQ 9.9.31.49738 安装器，从中提取该模块及同版 DLL、package.json 和 config.json。安装器 SHA-256 为 `0beb5cb4ff776cba822caa0abadbd53f5892f12628a86dabea1b372c82c086bc`。 |
| `node.exe` | Node.js 官方 `node-v22.11.0-win-x64.zip`，与编译时的 Node 版本独立。 |

完整的 QQ 文件清单、下载地址、组装步骤和原生模块加载检查均在标签的 `.github/workflows/release-publish.yml`（`assemble-windows-node`、`validate-windows-node`）。入口 `node.exe index.js` 把 wrapper 和 QQ 配置定位到包内，然后导入 Shell。用户机无需预装 QQ NT；此入口不启动系统 QQ 或向其进程注入。源码本身缺少 Node 和腾讯运行时，补齐固定外部输入即可组装独立运行目录。

本机 HTTP HEAD 实测，NapCat 固定 Release ZIP 返回 404，上游流程指定的 QQ 安装器地址返回 200。桌宠既有下载缓存中保留了 **124,089,498 字节的官方原版 ZIP**，`Get-FileHash -Algorithm SHA256` 实测为 `51fbb10e7bcd56b0c2a6de86c37811e7518330e48a137ac689ab1e4d8627d3f0`，与桌宠现有 pin 完全一致。本次本地打包选用此文件，沿用原哈希。桌宠仓库只保存构建逻辑、清单、说明和测试。

## 许可与交付范围

NapCat v4.18.41 的 LICENSE 署名 Copyright © 2024 Mlikiowa，规定非商业使用、再分发时随附许可全文及来源与版权。本机自用包保留原始 ZIP 与标签内 LICENSE 的完整字节，并标注各组件归属。Whalemate 的 MIT 许可仅覆盖项目自己的内容。腾讯运行时另有条款，当前查到的 QQ 协议第 2.4、8.2.2 条规定个人使用范围、备份和复制等限制。未找到允许 Whalemate 将 QQ 二进制随包对外分发的授权；对外分发可以保留用户提供原包的安装入口。如需分发含腾讯文件的版本，须核实相应权利。[QQ 软件许可及服务协议](https://private-70457.njc.vod.tencent-cloud.com/902a63d1-8683-11f0-b83f-f69ce6aefb15)

## Windows 验收边界

在全新的临时组件目录启动，分别记录 WebUI、登录二维码和 AstrBot 的 OneBot 监听状态。真实 NapCat → AstrBot 反向连接取决于账号完成登录；由负责人扫码后单独验收，测试不能将端口监听算作已经连接。

## 本地打包与安装

QQ 高级设置接受原始 ZIP 的路径，也接受含同名 ZIP 的目录。桌宠依次检查 `third-party/napcat/`、用户选择的来源、已校验下载缓存和固定官方 URL。前两处只解压本次拷贝并核对过哈希的快照。所有候选失效时仅尝试一次官方来源，错误提示列出缺失或哈希不匹配、HTTP 故障及重新选择原包的办法。

复验时从任务 worktree 运行下列 PowerShell 命令，构建输入为本机已经留存的官方原包和固定标签内的许可证。普通发行包与本机 QQ 包使用不同输出目录。

```powershell
$napcatZip = Join-Path $env:LOCALAPPDATA 'AICompanion\qq\downloads\NapCat.Shell.Windows.Node.zip'
.\.venv\Scripts\python.exe tools\build_release.py --napcat-zip $napcatZip --napcat-repo 'D:\Study\napcat\NapCatQQ'
$bundle = 'dist\local-napcat\Whalemate-v0.1.0-windows-x64.zip'
.\.venv\Scripts\python.exe tools\verify_release.py $bundle --case qq
.\.venv\Scripts\python.exe tools\verify_release.py $bundle --case smoke
```

## 2026-11-12 Windows 实测

- 最终 Whalemate ZIP 为 544,637,281 字节，SHA-256 为 `df0237e931a68b06a889286de4a13fb294e63171bdcfc158f3d8f25604c7278b`。内层原版 NapCat ZIP 的哈希仍是 `51fbb10e…627d3f0`，独立目录中附有固定提交的 LICENSE（SHA-256 `2bbc0dba0c62fcde4adfe38ebadad0b7d4e23b06b88d9551904bbe07769dc46f`）和 NOTICE.txt。
- `--case qq` 从最终 ZIP 解压并启动真实 EXE，自动发现随包原版 ZIP，从空目录安装 uv、Python、AstrBot。NapCat 和 AstrBot 均在运行，NapCat WebUI 鉴权成功，临时目录出现登录二维码 PNG，AstrBot 的 OneBot 端口可达，桌宠收到 AstrBot 插件心跳。这七项均为 true，退出码 0。登录前 `onebot_connected` 为 false。
- 另在临时实例用本机 WebSocket 客户端携带配置的 OneBot 令牌访问 `/ws`，得到 HTTP 101，并核对 `Sec-WebSocket-Accept`。探针未发送任何 QQ 消息。
- 同一最终 ZIP 的 `--case smoke` 通过桌面和桌宠两项 GUI smoke。QQ 准备与发布清单专项 `python -m unittest tests.test_qq_setup tests.test_release.ReleaseTests` 通过 44 项。
- 首轮组件试验中，Windows Code Integrity 事件 3077 记录 Python 3.12.15 的 `DLLs/sqlite3.dll` 被拦，AstrBot 首次退出。以相同文件重新启动后监听及心跳正常。最终包的主程序也曾在首次启动时返回 WinError 4551，同一 SHA 的正常复测通过。其他机器的应用控制策略仍需各自验证。
- 负责人尚未扫码。真实 NapCat → AstrBot 账号连接、接收和发送 QQ 消息需在登录后验收。测试结束已停止临时服务。
- 完整检查使用 `tools\run_checks.bat`。当前开发环境最初缺少项目已固定的 `lunar_python==1.4.9`，补到工作树独立的 `output/build-deps` 后通过农历专项 28 项。前两轮还各有一次 DSH 或 MCP 本机 HTTP 连接重置，单独复测均通过。最终全量执行 1,256 项（1,249 通过、7 跳过）。
