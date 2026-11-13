# V4-6c 本地中文向量模型

Codex 实现、Claude 审查。基线为 #62 合入提交 `b9af3af`。本项处理 #62 的三个 P3。

## 首次启用

在“设置 → 记忆与心事 → 语义召回与阶段摘要”选择“本地 · BGE 中文（512 维）”，点击启用并确认下载。状态栏显示下载进度和实际生效模式。“关闭语义召回”可取消下载；再次启用会复用哈希校验合格的文件，未完成的单文件从头重试。下载或校验失败会回到关键词召回，原有聊天仍可使用。切换回云端时重新确认目标账户和地址。

两份文件从固定 HTTPS 地址下载到**当前用户数据库所在目录**下的 `models/bge-small-zh-v1.5-int8/75c43b069aac4d136ba6bc1122f995fedcfd2781/`。GET 只取公开模型，临时文件经大小和 SHA-256 校验后原子替换，加载前再校验。首次安装需要联网；重启时缓存缺失或损坏会提示手动重试。记忆、查询使用本机 CPU 推理，云端 Key、额度和嵌入客户端均不会参与。切换模式会取消待返回的云端向量请求。

## 模型来源、许可和依赖

2026-11-13 核对 BAAI 模型卡标注 MIT，允许商用、修改和再分发；再分发须保留上游版权和许可声明。Xenova 的模型卡明确标识同一基础模型和 ONNX 转换来源。应用分发推理代码，用户按需下载模型。模型和分词文件均未提交或列入安装资源。[BAAI 固定版本模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/7999e1d3359715c523056ef9478215996d62a620/README.md) · [FlagEmbedding 的 MIT 许可](https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE) · [Xenova 固定转换版本](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)

| 文件 | 字节 | SHA-256 |
| --- | ---: | --- |
| onnx/model_quantized.onnx | 24,010,842 | `15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc` |
| tokenizer.json | 439,125 | `48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26` |

`onnxruntime==1.31.1` 提供 Windows Python 3.14 CPU 推理；`tokenizers==0.23.3` 按配套 tokenizer.json 的词表和规则分词。启用本地后才在后台导入两者。最多 512 token，取最后一层 CLS 并 L2 归一化，查询带上游推荐中文检索前缀。NumPy 由 ONNX Runtime 依赖提供，云端检索也可独立使用纯 Python 回退。[onnxruntime 1.31.1](https://pypi.org/project/onnxruntime/1.31.1/) · [tokenizers 0.23.3](https://pypi.org/project/tokenizers/0.23.3/)

## 索引和延迟

模型、来源、维度变化按角色重建，已存的目标签名标记跨重启有效。新增和编辑事实时，其余同来源、同角色、同频道、内容哈希仍匹配的向量照常召回，后台补齐缺项。删除和权限检查沿用 #62。

向量已归一化，NumPy 一次计算该频道候选的点积；没有 NumPy 时先用现有来源和权限预筛，再按最新记忆卡、最新存档的顺序至多计算 256 条。查询请求使用 1.5 秒连接／读取超时，后台批量索引保留 4 秒。超时后沿用原有失败退避。

## Windows 实测

2026-11-13，Intel Core i5-14600KF，Python 3.14 x64，NumPy 2.5.4。使用临时库和合成文本。

| 条件 | 工作集 MiB | 专用提交 MiB |
| --- | ---: | ---: |
| 关闭向量 | 445.6 | 342.4 |
| 真实本地模型完成首批推理后常驻 | 541.2 | 1035.6 |
| 增量 | **95.6** | **693.2** |

`tools\measure_pet.ps1` 和 `tools\measure_pet.ps1 -LocalEmbeddingDir output\v2-check\local-embedding-model` 各启动一个临时预览，按其进程树统计；就绪后稳定 15 秒，再采样 10 秒。工作集和进程专用提交分别按 Windows WorkingSet64 和 PrivateMemorySize64 计量。启用后已超过项目 350 MiB 常驻目标。独立进程导入本地服务模块时，onnxruntime、tokenizers、numpy 三者均未加载。通过真实下载器获取公开模型后，阻断 socket.connect 对两段合成文本进行 512 维检索，猫相关句得分 0.6052，打印机句得分 0.1368。

`python tools\measure_semantic.py --model-dir output\v2-check\local-embedding-model` 热身后运行。2000 × 1024 维 float32 合成向量，9 次点积打分中位数为原逐行 Python **72.66 ms**、NumPy 批量 **2.32 ms**。纯 Python 回退最多计算 256 条，中位数 **9.14 ms**。这里仅测打分，不含 SQLite 检索或模型推理。

从 ChatController.generate 到流式产出第一段，使用即时回复的假聊天模型；正常场景取 5 次中位数，超时取 3 次。关闭向量 **34.80 ms**，本机已热模型 **38.88 ms**，正常回环向量接口 **46.40 ms**。同一向量服务故意延迟 5 秒时，原 4 秒超时为 **4053.92 ms**，新 1.5 秒超时为 **1570.54 ms**。未测公网厂商真实首字、持续高负载、安装包运行和人工界面体验。
