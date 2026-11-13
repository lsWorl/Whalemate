"""文件下载、取消和向量打分都使用临时或合成的数据。"""
from array import array
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import random
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from aicompanion.domain.cancellation import CancellationToken, ReplyCancelled
from aicompanion.services import local_embedding as local
from aicompanion.services.semantic import FALLBACK_LIMIT, rank_vectors


class LocalFilesTests(unittest.TestCase):
    def test_sha_failure_cancel_retry_and_cached_offline_load(self):
        expected = b'fake-model'
        manifest = [('onnx/model.onnx', len(expected), sha256(expected).hexdigest())]
        with TemporaryDirectory() as directory, patch.object(local, 'FILES', manifest):
            root = Path(directory)
            opener = Mock()
            def fetch(body, token, progress=lambda _: None):
                opener.open.side_effect = lambda *args, **kwargs: BytesIO(body)
                return local.model_files(root, token, True, progress, opener)
            with self.assertRaisesRegex(local.ModelFilesError, 'SHA-256'):
                fetch(b'x'*len(expected), CancellationToken())
            self.assertEqual(list(root.iterdir()), [])
            cancelled = CancellationToken()
            with self.assertRaises(ReplyCancelled):
                fetch(expected, cancelled, lambda _: cancelled.cancel())
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(fetch(expected, CancellationToken()), [expected])
            opener.reset_mock()
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('请求了网络')):
                self.assertEqual(local.model_files(root, CancellationToken(), False, Mock(), opener), [expected])
                (root/'model.onnx').write_bytes(b'z'*len(expected))
                with self.assertRaises(local.ModelFilesError):
                    local.model_files(root, CancellationToken(), False, Mock(), opener)
            opener.open.assert_not_called()

    def test_unreachable_mirror_falls_back_and_reports_source_and_reason(self):
        body = b'synthetic model'
        manifest = [('onnx/model.onnx', len(body), sha256(body).hexdigest())]
        expected_urls = [f'https://{host}/Xenova/bge-small-zh-v1.5/resolve/{local.REVISION}/onnx/model.onnx'
                         for host in ('hf-mirror.com', 'huggingface.co')]
        with TemporaryDirectory() as directory, patch.object(local, 'FILES', manifest):
            root = Path(directory)
            opener = Mock()
            opener.open.side_effect = [HTTPError(expected_urls[0], 503, 'unavailable', {}, None), BytesIO(body)]
            messages = []
            self.assertEqual(local.model_files(root, CancellationToken(), True, messages.append, opener), [body])
            self.assertEqual([c.args[0] for c in opener.open.call_args_list], expected_urls)
            self.assertEqual((root/'model.onnx').read_bytes(), body)
            self.assertIn('hf-mirror.com · HTTP 503', messages[-1])
            self.assertIn('huggingface.co · 校验通过', messages[-1])

    def test_corrupt_mirror_never_publishes_file_before_fallback(self):
        good = b'good'
        manifest = [('model.onnx', 4, sha256(good).hexdigest())]
        with TemporaryDirectory() as directory, patch.object(local, 'FILES', manifest):
            root = Path(directory)
            def fetch(url, timeout):
                if url.startswith('https://hf-mirror.com/'):
                    return BytesIO(b'evil')  # 正确大小、错误 SHA。
                self.assertFalse((root/'model.onnx').exists())
                raise TimeoutError()
            opener = Mock()
            opener.open.side_effect = fetch
            with self.assertRaises(local.ModelFilesError) as failure:
                local.model_files(root, CancellationToken(), True, Mock(), opener)
            self.assertIn('hf-mirror.com · SHA-256 校验失败', str(failure.exception))
            self.assertIn('huggingface.co · 连接或读取超时', str(failure.exception))
            self.assertEqual(list(root.iterdir()), [])
            opener.open.side_effect = [BytesIO(b'evil'), BytesIO(good)]
            self.assertEqual(local.model_files(root, CancellationToken(), True, Mock(), opener), [good])
            self.assertEqual((root/'model.onnx').read_bytes(), good)

    def test_cancel_during_failed_request_does_not_contact_next_source(self):
        token = CancellationToken()
        def cancelled(url, timeout):
            token.cancel()
            raise TimeoutError()
        manifest = [('tokenizer.json', 4, '0'*64)]
        with TemporaryDirectory() as directory, patch.object(local, 'FILES', manifest):
            opener = Mock()
            opener.open.side_effect = cancelled
            with self.assertRaises(ReplyCancelled):
                local.model_files(Path(directory), token, True, Mock(), opener)
            opener.open.assert_called_once()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_scoring_agrees_with_scalar_and_fallback_is_bounded(self):
        rng = random.Random(216)
        def vector():
            return array('f', (rng.uniform(-1, 1) for _ in range(128)))
        needle = vector()
        blobs = [(i, vector().tobytes()) for i in range(FALLBACK_LIMIT + 12)]
        expected = [(k, sum(x*y for x, y in zip(needle, memoryview(blob).cast('f')))) for k, blob in blobs]
        for (k, actual), (i, score) in zip(rank_vectors(needle, blobs), expected):
            self.assertEqual(k, i)
            self.assertAlmostEqual(actual, score, places=5)
        with patch.dict(sys.modules, {'numpy': None}):
            self.assertEqual(rank_vectors(needle, blobs), expected[:FALLBACK_LIMIT])
