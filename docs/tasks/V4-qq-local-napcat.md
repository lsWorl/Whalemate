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
