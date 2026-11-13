"""云端召回与摘要只用临时库、合成记忆和模拟请求。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aicompanion.domain.cancellation import CancellationToken
from aicompanion.providers.admin import ModelAdmin
from aicompanion.providers.credentials import CredentialStore
from aicompanion.providers.router import ModelRouter
from aicompanion.services import semantic, stages
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.context_service import ContextService
from aicompanion.usage import meter
from test_recall import Base, CID


class VectorTests(Base):
    def setUp(self):
        super().setUp()
        self.env = {'VECTOR_TEST_KEY': 'synthetic'}
        self.router = ModelRouter(self.db, credentials=CredentialStore(self.db, self.env, None))
        self.admin = ModelAdmin(self.router)
        self.account = self.admin.add_account('bailian', key_env='VECTOR_TEST_KEY')
        self.profile = next(m.id for m in self.admin.models('embedding') if m.model_id == 'text-embedding-v4')
        self.clock = 100
        self.calls = []
        self.token = CancellationToken()
        def vectors(target, key, texts, cid, cancellation):
            self.calls.append(list(texts))
            return [semantic.normalize(([1, 0] if '楼' in t or '住所' in t else [0, 1]) + [0]*(target.dimension-2),
                                       target.dimension) for t in texts]
        self.index = semantic.SemanticRecall(self.db, self.router, vectors, lambda: self.clock)
        self.index.start = Mock()

    def add(self, text, key, channel='desktop', old=False, private=False):
        mid = self.db.add_message('user', text, CID, channel=channel)
        scope = 'private' if private else 'pet' if channel == 'desktop' else 'qq'
        self.db.upsert_memory(text, CID, key, source_ids=[mid], scope=scope)
        row = next(r for r in self.db.memory_card(CID) if r['fact_key'] == key)
        if old:
            self.db.forget_memories([row['id']])
        return row['id']

    def hits(self, query='新住所', channel='desktop'):
        return self.index.search(CID, query, 'pet' if channel == 'desktop' else 'qq', channel, self.token)

    def ready(self):
        self.index.enable(self.profile, 256)
        for _ in range(12):
            if not self.index.index_batch(CID):
                break
        else:
            self.fail('索引没有完成')

    def test_batch_hybrid_rank_dedupe_and_private_channels(self):
        self.add('去年搬进了小红楼', 'home', old=True)
        self.add('去年搬进了小红楼', 'copy')
        self.add('原先养过雪貂', 'animal', old=True)
        self.add('甲群的办公楼', 'group-a', 'qq:group:1')
        self.add('乙群的小楼', 'group-b', 'qq:group:2')
        self.add('群里后来标成私下的事', 'locked', 'qq:group:1', private=True)
        self.ready()
        self.assertNotIn('群里后来标成私下的事', [t for batch in self.calls for t in batch])
        self.assertTrue(all(not ('甲群的办公楼' in b and '乙群的小楼' in b) for b in self.calls))
        hits = self.hits()
        self.assertEqual({h[0] for h in hits}, {'home', 'copy'})
        recalled = self.recall.recall(CID, '新住所', semantic_hits=hits)
        self.assertEqual(recalled.render().count('小红楼'), 1)
        self.assertIn('旧事', recalled.render())
        self.assertNotIn('雪貂', recalled.render())
        self.assertEqual({h[0] for h in self.hits(channel='qq:group:2')}, {'group-b'})
        character = next(c for c in self.db.characters() if c.id == CID)
        _, prompt = ContextService(self.db, MemoryService(self.db), self.index).build(character, '办公楼', 'qq:group:2', 'qq')
        self.assertNotIn('甲群的办公楼', prompt)
        self.assertIn('乙群的小楼', prompt)

    def test_delete_and_update_after_request_do_not_resurrect(self):
        mid = self.add('旧楼', 'home')
        self.ready()
        hits = self.hits()
        self.db.upsert_memory('搬到海边', CID, 'home')
        self.assertFalse(self.recall.recall(CID, '住所', semantic_hits=hits))
        self.db.delete_memory(mid, CID)
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM memory_vectors').fetchone()[0], 0)
        self.assertFalse(self.db.upsert_memory('旧楼', CID, 'home'))
        self.assertFalse(self.recall.recall(CID, '住所', semantic_hits=hits))

    def test_deleting_memory_during_query_cancels_stale_prompt(self):
        from aicompanion.domain.cancellation import ReplyCancelled
        mid = self.add('之前住的旧楼', 'home')
        self.ready()
        self.db.add_message('user', '新住所在哪里', CID)
        character = next(c for c in self.db.characters() if c.id == CID)
        original = self.index.client
        def during_request(*args):
            self.db.delete_memory(mid, CID)
            return original(*args)
        with patch.object(self.index, 'client', side_effect=during_request):
            with self.assertRaises(ReplyCancelled):
                ContextService(self.db, MemoryService(self.db), self.index).build_with_sections(
                    character, '住所', vector_query=True, cancellation=self.token)

    def test_archive_uses_latest_scope_even_when_previously_public(self):
        self.add('海边小楼', 'home', old=True)
        self.add('海边私人小楼', 'home', old=True, private=True)
        self.assertFalse(self.recall.recall(CID, '海边小楼', 'qq', 'qq:group:1'))

    def test_public_extraction_cannot_overwrite_other_group(self):
        self.add('甲群的合成记忆', 'qq#1:job', 'qq:group:1')
        mid = self.db.add_message('user', '乙群消息', CID, channel='qq:group:2')
        content = '乙群想写入同一个事实键'
        guard = MemoryService.public_guard(content, CID, 'qq:group:2')
        self.assertFalse(self.db.upsert_memory(content, CID, 'qq#1:job', source_ids=[mid], scope='qq', guard=guard))

    def test_missing_or_edited_fact_uses_other_current_vectors(self):
        self.add('老街红楼', 'home')
        self.add('上班的楼', 'office')
        self.ready()
        self.index.start.reset_mock()
        self.db.upsert_memory('改为海边', CID, 'office')
        self.add('去年新建的楼', 'new')
        self.assertEqual([k for k, _, _ in self.hits()], ['home'])
        self.index.start.assert_called_once()

    def test_local_encoding_never_uses_cloud_even_after_failure_or_restart(self):
        from unittest.mock import patch
        from aicompanion.services import local_embedding
        from aicompanion.services.semantic import LOCAL

        self.add('小楼房', 'home')
        self.ready()
        encoder = Mock()
        encoder.encode.side_effect = lambda texts, token, **kw: [semantic.normalize([1]+[0]*511, 512) for _ in texts]
        with patch.object(self.index, 'client', side_effect=AssertionError('调用了云端')) as cloud, \
                patch.object(semantic.net, 'fetch', side_effect=AssertionError('发出网络请求')) as network, \
                patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('本地运行联机')) as http, \
                patch.object(local_embedding, 'LocalEncoder', return_value=encoder) as load:
            self.index.enable_local(download=False)
            self.index.loader.join(5)
            self.assertEqual(self.db.get_setting(semantic.SETTING), vars(LOCAL))
            self.assertFalse(self.hits())
            self.assertTrue(self.index.index_batch(CID))
            self.assertTrue(self.hits())
            scopes = [call.kwargs for call in encoder.encode.call_args_list]
            self.assertEqual(scopes, [{'query': False}, {'query': True}])
            self.index.close()
            restarted = semantic.SemanticRecall(self.db, self.router, cloud)
            self.assertIsNone(restarted.target())
            restarted.loader.join(5)
            self.assertIs(restarted.target()[1], encoder)
            self.assertFalse(load.call_args.args[2])  # 重启只加载缓存。
            restarted.close()
            self.index.disable()
            cloud.assert_not_called()
            network.assert_not_called()
            http.assert_not_called()
        with patch.object(local_embedding, 'LocalEncoder', side_effect=OSError), \
                patch.object(self.index, 'client') as cloud:
            fresh = semantic.SemanticRecall(self.db, self.router, cloud)
            fresh.start = Mock()
            fresh.enable_local(download=False)
            fresh.loader.join(5)
            self.assertFalse(fresh.search(CID, '住所', 'pet', 'desktop', self.token))
            self.assertIn('关键词', fresh.state)
            cloud.assert_not_called()
            fresh.close()

    def test_switch_to_local_cancels_pending_cloud_request(self):
        from threading import Thread, Event
        self.add('家里小楼', 'home')
        self.ready()
        started = Event()
        cancelled = Event()
        def pending(target, key, texts, cid, token):
            started.set()
            if token.event.wait(3):
                cancelled.set()
            token.check()
            return []
        with patch.object(self.index, 'client', side_effect=pending), \
                patch.object(semantic.local, 'LocalEncoder', return_value=Mock()):
            thread = Thread(target=self.hits)
            thread.start()
            self.assertTrue(started.wait(2))
            self.index.enable_local(download=False)
            thread.join(3)
            self.index.loader.join(2)
            self.assertTrue(cancelled.is_set())
            self.assertFalse(self.token.event.is_set())
            self.assertEqual(self.index.failures, 0)
            self.index.close()

    def test_query_timeout_is_shorter_than_index_timeout(self):
        self.add('红楼', 'home')
        self.index.enable(self.profile, 256)
        self.index.client = semantic.embed
        data = {'data': [{'index': 0, 'embedding': [1]+[0]*255}]}
        with patch.object(semantic.net, 'fetch', return_value=json.dumps(data).encode()) as fetch:
            self.index.index_batch(CID)
            self.assertTrue(self.hits())
            self.assertEqual([c.kwargs['timeout'] for c in fetch.call_args_list], [4, 1.5])

    def test_new_dimension_and_model_wait_until_batch_complete(self):
        for i in range(12):
            self.add(f'海边楼房{i}', f'f{i}')
        self.ready()
        self.assertIn(10, list(map(len, self.calls)))
        for model, dim in [(self.profile, 512), (self.admin.models('embedding')[-1].id, 256)]:
            self.index.enable(model, dim)
            self.assertEqual(self.hits(), [])
            self.index.index_batch(CID)
            self.assertEqual(self.hits(), [])
            self.index.index_batch(CID)
            self.assertTrue(self.hits())
        with self.db.transaction() as c:
            self.assertEqual(c.execute('SELECT COUNT(DISTINCT model) FROM memory_vectors').fetchone()[0], 1)

    def test_consent_offline_quota_backoff_endpoint_change(self):
        self.add('我搬进了小楼房', 'h', old=True)
        self.assertFalse(self.index.index_batch(CID))
        self.assertFalse(self.hits())
        self.ready()
        before = len(self.calls)
        self.env.clear()
        self.assertFalse(self.hits())
        self.env['VECTOR_TEST_KEY'] = 'synthetic'
        self.router.settings = SimpleNamespace(offline=True)
        self.assertFalse(self.hits())
        self.router.settings.offline = False
        self.db.set_setting('quota_daily:embedding', 0.1)
        self.db.add_token_usage('embedding', 'test', 100, 0, cost=0.2)
        self.assertFalse(self.hits())
        self.assertEqual(len(self.calls), before)
        self.db.set_setting('quota_daily:embedding', 0)
        with patch.object(self.index, 'client', side_effect=TimeoutError) as request:
            self.assertFalse(self.hits())
            self.assertFalse(self.hits())
            self.assertEqual(request.call_count, 1)
        self.assertIn('小楼房', self.recall.recall(CID, '小楼房', semantic_hits=self.hits()).render())
        self.clock += 5
        self.assertTrue(self.hits())
        self.admin.update_account(self.account.id, base_url='https://example.invalid/v1')
        self.assertFalse(self.hits())
        self.assertIn('重新确认', self.index.state)

    def test_background_index_survives_restart_and_schema_migration(self):
        from threading import Event
        from aicompanion.storage.database import Database
        from aicompanion.storage.schema import SCHEMA_VERSION

        mid = self.add('小楼的来历', 'home')
        with self.db.transaction() as c:
            c.execute('DROP TABLE memory_vectors')
            c.execute('DROP TABLE stage_summaries')
            c.execute('PRAGMA user_version=18')
        Database(self.path).initialize()
        with self.db.transaction() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(c.execute('SELECT content FROM memories WHERE id=?', (mid,)).fetchone()[0], '小楼的来历')
        self.assertTrue(list(self.path.parent.glob('*.backup-*.sqlite3')))
        completed = Event()
        worker = semantic.SemanticRecall(self.db, self.router, self.index.client)
        original = worker.index_batch
        def index(cid):
            result = original(cid)
            if result:
                completed.set()
            return result
        worker.index_batch = index
        try:
            worker.enable(self.profile, 256)
            self.assertTrue(completed.wait(5))
        finally:
            worker.close()
        self.assertFalse(worker.worker.is_alive())
        before = list(self.calls)
        self.assertFalse(self.index.index_batch(CID))
        self.assertEqual(self.calls, before)
        self.assertEqual([k for k, _, _ in self.hits()], ['home'])

    def test_wire_format_order_and_daily_usage(self):
        self.index.enable(self.profile, 256)
        target, key = self.index.target()
        data = [{'index': i, 'embedding': [int(j == i) for j in range(256)]} for i in (1, 0)]
        response = {'data': data, 'usage': {'total_tokens': 3}}
        with patch.object(semantic.net, 'fetch', return_value=json.dumps(response).encode()) as fetch, \
                patch.object(meter, '_recorder') as record:
            values = semantic.embed(target, key, ['甲', '乙'], CID, self.token)
            self.assertEqual([values[0][0], values[1][1]], [1, 1])
            request = fetch.call_args.args[0]
            self.assertEqual(request.full_url, target.endpoint + '/embeddings')
            self.assertEqual(json.loads(request.data)['input'], ['甲', '乙'])
            self.assertEqual(record.call_args.args[:3], ('embedding', target.model, 3))
            data[0]['index'] = 0
            fetch.return_value = json.dumps(response).encode()
            with self.assertRaises(ValueError):
                semantic.embed(target, key, ['甲', '乙'], CID, self.token)


class StageTests(Base):
    def test_stage_sources_scope_and_forgetting_watermark(self):
        channel = 'qq:group:1'
        ids = self.fill_chat(46, channel, '水族馆原话{}')
        self.fill_chat(5, 'qq:group:2', '别群秘密{}')
        model = Mock()
        model.generate_reply_stream.return_value = ['{"summary":"几位群友讨论了参观水族馆。"}']
        def compact():
            stages.compact(self.db, CID, channel, model, CancellationToken())
        compact()
        compact()
        self.assertEqual(model.generate_reply_stream.call_count, 1)
        self.assertNotIn('别群秘密', model.generate_reply_stream.call_args.args[0][0].content)
        stage, = stages.recent(self.db, CID, channel, ids[16])
        self.assertEqual(json.loads(stage['source_ids']), ids[:16])
        self.assertEqual(stages.recent(self.db, CID, channel, ids[10]), [])
        self.assertEqual(stages.recent(self.db, CID, 'desktop', 1<<55), [])
        self.assertEqual([r['id'] for r in stages.sources(self.db, CID, stage['id'])], ids[:16])
        character = next(c for c in self.db.characters() if c.id == CID)
        _, prompt = ContextService(self.db, MemoryService(self.db)).build(character, '水族馆', channel, 'qq')
        self.assertIn('群友讨论了参观水族馆', prompt)
        self.assertNotIn('水族馆原话0', prompt)
        self.db.upsert_memory('删除测试', CID, 'del')
        self.db.delete_memory(self.db.memory_card(CID)[0]['id'], CID)
        self.assertEqual(stages.sources(self.db, CID, stage['id']), [])
        self.assertEqual(len(self.db.recent_messages(100, CID, channel=channel)), 46)
        self.assertEqual(stages.recent(self.db, CID, channel, 1<<55), [])

    def test_deletion_during_summarization_prevents_late_write(self):
        self.fill_chat(46)
        self.db.upsert_memory('待删除', CID, 'secret')
        mid = self.db.memory_card(CID)[0]['id']
        def respond(*_):
            self.db.delete_memory(mid, CID)
            yield '{"summary":"失效的内容"}'
        model = Mock()
        model.generate_reply_stream.side_effect = respond
        stages.compact(self.db, CID, 'desktop', model, CancellationToken())
        self.assertEqual(stages.recent(self.db, CID, 'desktop', 1 << 50), [])
        self.assertEqual(len(self.db.recent_messages(50, CID)), 46)

    def test_full_request_can_drop_stage_and_recall_without_losing_current(self):
        from aicompanion.controllers.chat_controller import ChatController
        from aicompanion.llm.base import ChatTurn
        controller = ChatController(self.db, MemoryService(self.db), Mock(spec=['generate_reply_stream']))
        turns = [ChatTurn('user', '当前这句')]
        with patch('aicompanion.services.budget.LIMITS', {'default': 32}):
            history, prompt = controller._fit(CID, 'default', turns, '人格'+'旧事'*80+'规则',
                {'_optional_start': 2, 'recall': 80, 'stages': 80}, {'base': 162})
        self.assertEqual((history, prompt), (turns, '人格规则'))
        self.assertEqual(self.db.get_setting('context_report:'+CID)['sections']['stages'], 0)
