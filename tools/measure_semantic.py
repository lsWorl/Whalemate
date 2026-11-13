"""合成数据实测：向量打分、聊天入口到第一个 token。只用临时库、回环向量端点和假聊天模型。"""
import argparse
from array import array
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import random
from statistics import median
from tempfile import TemporaryDirectory
import threading
from time import perf_counter, sleep
from unittest.mock import patch

from aicompanion.controllers.chat_controller import ChatController
from aicompanion.domain.cancellation import CancellationToken
from aicompanion.providers.admin import ModelAdmin
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.router import ModelRouter
from aicompanion.services import semantic
from aicompanion.services.memory_service import MemoryService
from aicompanion.storage.database import Database


def milliseconds(fn, rounds):
    samples = []
    for _ in range(rounds):
        start = perf_counter()
        fn()
        samples.append(round((perf_counter() - start)*1000, 2))
    return {'median_ms': median(samples), 'samples_ms': samples}


def rank_benchmark():
    rng = random.Random(62)
    def vector():
        return semantic.normalize([rng.uniform(-1, 1) for _ in range(1024)], 1024)
    needle = vector()
    blobs = [(i, vector().tobytes()) for i in range(2000)]
    def previous():
        return [(k, sum(x*y for x, y in zip(needle, array('f', blob), strict=True))) for k, blob in blobs]
    previous()
    semantic.rank_vectors(needle, blobs)
    out = {'scalar_2000': milliseconds(previous, 9),
           'batch_2000': milliseconds(lambda: semantic.rank_vectors(needle, blobs), 9)}
    with patch.dict('sys.modules', {'numpy': None}):
        out['fallback_256'] = milliseconds(lambda: semantic.rank_vectors(needle, blobs), 9)
    return out


class Chat:
    def generate_reply_stream(self, history, prompt, cancellation):
        yield '好的，知道了。'


@contextmanager
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            values = [1] + [0]*(body['dimensions']-1)
            payload = json.dumps({'data': [{'index': i, 'embedding': values} for i in range(len(body['input']))]}).encode()
            sleep(self.server.delay)
            try:
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                pass
        def log_message(self, *args):
            pass
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        server.delay = 0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def first_token(model_dir):
    with TemporaryDirectory() as tmp, endpoint() as server:
        db = Database(Path(tmp)/'test.sqlite3')
        db.initialize()
        controller = ChatController(db, MemoryService(db), Chat())
        cid = controller.character.id
        service = controller.semantic
        router = ModelRouter(db, credentials=CredentialStore(db, {'BENCH_KEY': 'synthetic'}, None))
        service.router = router
        admin = ModelAdmin(router)
        account = admin.add_account(base_url=f'http://127.0.0.1:{server.server_port}/v1', key_env='BENCH_KEY')
        model = admin.add_model(account.id, 'synthetic-bench', ('embedding',))
        db.upsert_memory('上次在水族馆看了鲸鱼', cid, 'synthetic')
        through = db.add_message('user', '还记得那头鲸鱼吗', cid)
        number = 0
        def first():
            nonlocal number
            number += 1
            stream = controller.generate(f'bench{number}', controller.character, CancellationToken(),
                                         through_id=through, offer_tasks=False)
            try:
                next(stream)
            finally:
                stream.close()
        first()
        out = {'off': milliseconds(first, 5)}
        with patch.object(service, 'start'):
            service.enable(model.id, 256)
            service.index_batch(cid)
            first()
            out['loopback'] = milliseconds(first, 5)
            server.delay = 5
            for timeout in (4, 1.5):
                def slow():
                    service.retry_at = 0  # 单独测每次上游超时，排除后续退避直接命中。
                    first()
                with patch.object(semantic, 'QUERY_TIMEOUT', timeout):
                    out[f'timeout_{timeout}'] = milliseconds(slow, 3)
            if model_dir:
                server.delay = 0
                service.local_root = model_dir
                service.enable_local(download=False)
                service.loader.join(30)
                if not service.encoder or not service.index_batch(cid):
                    raise RuntimeError(service.state)
                first()
                out['local_hot'] = milliseconds(first, 5)
            service.close()
        return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', type=Path)
    args = parser.parse_args()
    print(json.dumps({'similarity': rank_benchmark(), 'first_token': first_token(args.model_dir)}, indent=2))
