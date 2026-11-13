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
                opener.open.return_value = BytesIO(body)
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
