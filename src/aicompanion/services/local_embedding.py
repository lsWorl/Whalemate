"""本地 BGE 推理和显式下载。查询、建索引只接收已载入的 CPU 会话。"""
from array import array
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

REVISION = '75c43b069aac4d136ba6bc1122f995fedcfd2781'
MODEL = 'bge-small-zh-v1.5-int8'
DIMENSION = 512
BASE = f'https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/{REVISION}/'
FILES = (
    ('onnx/model_quantized.onnx', 24010842, '15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc'),
    ('tokenizer.json', 439125, '48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26'),
)


class ModelFilesError(ValueError):
    pass


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise ModelFilesError('下载服务要求使用非 HTTPS 地址')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def verified(path, size, checksum):
    if not path.is_file() or path.stat().st_size != size:
        return None
    content = path.read_bytes()
    return content if sha256(content).hexdigest() == checksum else None


def model_files(root, token, download, progress, opener=None):
    """固定公开 URL 不携带凭据。临时文件逐块写，校验通过后才原子替换，失败可从头重试。"""
    assets = []
    for remote, size, checksum in FILES:
        token.check()
        path = root / Path(remote).name
        content = verified(path, size, checksum)
        if content is None:
            if not download:
                raise ModelFilesError('本地模型缺失或校验失败，请在记忆设置里重试下载')
            root.mkdir(parents=True, exist_ok=True)
            opener = opener or build_opener(SecureRedirect())
            with NamedTemporaryFile(dir=root, prefix=path.name + '.', suffix='.part', delete=False) as temp:
                part = Path(temp.name)
            try:
                total = 0
                hasher = sha256()
                with opener.open(BASE + remote, timeout=8) as response, part.open('wb') as stream:
                    while True:
                        token.check()
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > size:
                            raise ModelFilesError('模型文件大小与发布记录不符')
                        stream.write(chunk)
                        hasher.update(chunk)
                        progress(f'正在下载 {path.name}，{total*100//size}%')
                token.check()
                if total != size or hasher.hexdigest() != checksum:
                    raise ModelFilesError('下载未通过 SHA-256 校验，请重试')
                part.replace(path)
            finally:
                part.unlink(missing_ok=True)
            content = verified(path, size, checksum)
            if content is None:
                raise ModelFilesError('本地模型校验失败，请重试')
        assets.append(content)
    return assets


class LocalEncoder:
    """加载和调用均在后台执行；构造完成后仅使用已校验的内存数据。"""
    def __init__(self, root, token, download, progress):
        model, vocabulary = model_files(root, token, download, progress)
        token.check()
        import onnxruntime as ort
        from tokenizers import Tokenizer

        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.tokenizer = Tokenizer.from_str(vocabulary.decode('utf-8'))
        self.tokenizer.enable_truncation(max_length=512)
        self.session = ort.InferenceSession(model, sess_options=options, providers=['CPUExecutionProvider'])
        token.check()

    def encode(self, texts, cancellation, *, query=False):
        import numpy as np

        cancellation.check()
        if query:
            texts = ['为这个句子生成表示以用于检索相关文章：' + t for t in texts]
        # 至多十条短记忆。不为分词常驻一个按 CPU 核数扩展的 Rayon 线程池。
        batch = [self.tokenizer.encode(text) for text in texts]
        width = max(len(item.ids) for item in batch)
        inputs = {}
        for name, field in (('input_ids', 'ids'), ('attention_mask', 'attention_mask'), ('token_type_ids', 'type_ids')):
            data = np.zeros((len(batch), width), dtype=np.int64)
            for i, item in enumerate(batch):
                values = getattr(item, field)
                data[i, :len(values)] = values
            inputs[name] = data
        hidden, = self.session.run(['last_hidden_state'], inputs)
        cancellation.check()
        cls = hidden[:, 0, :]
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        if cls.shape != (len(texts), DIMENSION) or not np.isfinite(norms).all() or (norms == 0).any():
            raise ValueError('本地模型产生无效向量')
        return [array('f', row.tobytes()) for row in cls / norms]
