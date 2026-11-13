# V4-6c 本地中文向量模型

Codex 实现、Claude 审查。基线 b9af3af。采用 BAAI/bge-small-zh-v1.5 的量化 ONNX，首次启用下载并验 SHA-256。CPU 推理采用 onnxruntime，使用与权重配套的 tokenizers 分词器。两项依赖按 Windows Python 3.14 可用的 wheel 锁版本，延迟导入。
