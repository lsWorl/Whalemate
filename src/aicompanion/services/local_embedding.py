"""本地 BGE 推理和显式下载。查询、建索引只接收已载入的 CPU 会话。"""
from array import array
from hashlib import sha256
from http.client import HTTPException
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

REVISION = '75c43b069aac4d136ba6bc1122f995fedcfd2781'
MODEL = 'bge-small-zh-v1.5-int8'
DIMENSION = 512
SOURCES = ('https://hf-mirror.com', 'https://huggingface.co')
MODEL_PATH = f'/Xenova/bge-small-zh-v1.5/resolve/{REVISION}/'
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


def fetch_file(url, size, checksum, token, report, opener):
    """按发布大小限制读取。传输或校验失败由调用方更换来源，本地写入失败不重复下载。"""
    try:
        with opener.open(url, timeout=8) as response:
            buffer = BytesIO()
            total = 0
            while total <= size:
                token.check()
                chunk = response.read(min(128 * 1024, size + 1 - total))
                if not chunk:
                    break
                buffer.write(chunk)
                total += len(chunk)
                if total <= size:
                    report(f'已下载 {total*100//size}%')
    except HTTPError as error:
        error.close()
        raise ModelFilesError(f'HTTP {error.code}') from error
    except URLError as error:
        raise ModelFilesError(f'连接失败（{error.reason}）') from error
    except TimeoutError as error:
        raise ModelFilesError('连接或读取超时') from error
    except (OSError, HTTPException) as error:
        raise ModelFilesError(f'传输失败（{type(error).__name__}）') from error
    token.check()
    content = buffer.getvalue()
    if len(content) != size:
        raise ModelFilesError(f'大小不符（收到 {len(content):,} 字节，预期 {size:,}）')
    if sha256(content).hexdigest() != checksum:
        raise ModelFilesError('SHA-256 校验失败')
    return content


def model_files(root, token, download, progress, opener=None):
    """逐文件按固定顺序尝试公开 HTTPS 来源，只有验证通过的内容才能替换缓存。"""
    assets = []
    failures = []
    for remote, size, checksum in FILES:
        token.check()
        path = root / Path(remote).name
        content = verified(path, size, checksum)
        if content is None:
            if not download:
                raise ModelFilesError('本地模型缺失或校验失败，请在记忆设置里重试下载')
            root.mkdir(parents=True, exist_ok=True)
            opener = opener or build_opener(SecureRedirect())
            for source in SOURCES:
                host = urlsplit(source).hostname
                def report(status):
                    progress('；'.join([*failures, f'{path.name} · {host} · {status}']))
                report('正在连接')
                token.check()
                try:
                    content = fetch_file(source + MODEL_PATH + remote, size, checksum, token, report, opener)
                except ModelFilesError as error:
                    token.check()
                    failures.append(f'{path.name} · {host} · {error}')
                    continue
                break
            else:
                raise ModelFilesError('；'.join(failures))
            token.check()
            with NamedTemporaryFile(dir=root, prefix=path.name + '.', suffix='.part', delete=False) as temp:
                part = Path(temp.name)
            try:
                part.write_bytes(content)
                token.check()
                part.replace(path)
            finally:
                part.unlink(missing_ok=True)
            report('校验通过')
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
