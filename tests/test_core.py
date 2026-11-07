from contextlib import closing
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aicompanion.domain.character import DEFAULT_CHARACTER
from aicompanion.domain.cancellation import CancellationToken,ReplyCancelled
from aicompanion.storage.database import Database
from aicompanion.services.memory_service import MemoryService
from aicompanion.services.conversation_coordinator import ConversationCoordinator
from aicompanion.controllers.chat_controller import ChatController
from aicompanion.llm.mock_client import MockLLMClient
from aicompanion.llm.openai_compatible_client import OpenAICompatibleClient,LLMResponseError
from aicompanion.speech.output import SentenceBuffer
from aicompanion.audio.capture import UtteranceDetector
from aicompanion.config import load_settings


class ConfigurationTests(unittest.TestCase):
    def load(self,values):
        with patch.dict(os.environ,values,clear=True), patch('aicompanion.config.load_dotenv'):
            return load_settings()

    def test_different_providers_do_not_share_credentials(self):
        settings=self.load({'LLM_BASE_URL':'https://api.deepseek.com','LLM_API_KEY':'dummy-chat'})
        self.assertEqual(settings.speech_api_key,'')

    def test_explicit_speech_credential_is_independent(self):
        settings=self.load({'LLM_API_KEY':'dummy-chat','SPEECH_API_KEY':'dummy-speech'})
        self.assertEqual(settings.speech_api_key,'dummy-speech')

    def test_same_host_preserves_compatible_fallback(self):
        settings=self.load({'LLM_BASE_URL':'https://open.bigmodel.cn/api/paas/v4','LLM_API_KEY':'dummy-chat'})
        self.assertEqual(settings.speech_api_key,'dummy-chat')

    def test_explicit_empty_speech_key_disables_fallback(self):
        settings=self.load({'LLM_BASE_URL':'https://open.bigmodel.cn/api/paas/v4','LLM_API_KEY':'dummy-chat','SPEECH_API_KEY':''})
        self.assertEqual(settings.speech_api_key,'')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'test.sqlite3'
        self.db=Database(self.path); self.db.initialize()

    def tearDown(self): self.temp.cleanup()

    def test_character_isolation(self):
        self.db.add_character(replace(DEFAULT_CHARACTER,id='other',name='Other'))
        self.db.add_message('user','A',character_id='emilia')
        self.db.add_message('user','B',character_id='other')
        self.db.upsert_memory('private',fact_key='name')
        self.assertEqual([r['content'] for r in self.db.recent_messages(character_id='other')],['B'])
        self.assertEqual(self.db.recent_memories(character_id='other'),[])

    def test_memory_update_and_tombstone(self):
        self.db.upsert_memory('coffee',fact_key='drink')
        self.db.upsert_memory('tea',fact_key='drink')
        rows=self.db.recent_memories(); self.assertEqual(len(rows),1); self.assertEqual(rows[0]['content'],'tea')
        self.db.delete_memory(rows[0]['id'],'emilia')
        self.assertFalse(self.db.upsert_memory('tea again',fact_key='drink'))
        MemoryService(self.db).maybe_extract_memory('我喜欢茶')
        self.assertEqual(self.db.recent_memories(),[])

    def test_read_connection_closes(self):
        with self.db.transaction() as connection: connection.execute('SELECT 1')
        with self.assertRaises(sqlite3.ProgrammingError): connection.execute('SELECT 1')

    def test_proactive_claim_is_persistent(self):
        self.assertTrue(self.db.claim_proactive('emilia','morning'))
        self.assertFalse(Database(self.path).claim_proactive('emilia','morning'))

    def test_migration_preserves_data_and_backup(self):
        old=self.path.parent/'old.sqlite3'
        with closing(sqlite3.connect(old)) as connection:
            connection.executescript("""
            CREATE TABLE chat_messages(id INTEGER PRIMARY KEY,role TEXT,content TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE memories(id INTEGER PRIMARY KEY,content TEXT,importance INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE emotion_state(id INTEGER PRIMARY KEY,mood TEXT,affection INTEGER,loneliness INTEGER,energy INTEGER,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO chat_messages VALUES(1,'user','old text',CURRENT_TIMESTAMP);
            INSERT INTO memories VALUES(1,'old memory',2,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
            INSERT INTO emotion_state VALUES(1,'happy',70,10,80,CURRENT_TIMESTAMP);
            """)
        db=Database(old); db.initialize(); db.initialize()
        self.assertEqual(db.recent_messages()[0]['content'],'old text')
        self.assertEqual(db.recent_memories()[0]['content'],'old memory')
        self.assertEqual(db.load_emotion().affection,70)
        self.assertEqual(len(list(old.parent.glob('old.backup-*.sqlite3'))),1)

    def test_partial_failure_is_preserved(self):
        class Broken(MockLLMClient):
            def generate_reply_stream(self,*args,**kwargs):
                yield 'partial'
                raise LLMResponseError('broken')
        controller=ChatController(self.db,MemoryService(self.db),Broken())
        with self.assertRaises(LLMResponseError): list(controller.stream_user_message('hello'))
        row=self.db.recent_messages()[-1]
        self.assertEqual((row['content'],row['status']),('partial','failed'))

    def test_delivered_reply_is_not_replaced_by_generated_full_text(self):
        self.db.start_turn('turn', 'emilia')
        self.db.set_delivered_reply('turn', 'emilia', 'heard', 'generating')
        self.db.finish_turn('turn', 'emilia', 'heard and unheard', 'complete')
        self.db.set_delivered_reply('turn', 'emilia', 'heard', 'cancelled')
        rows=self.db.recent_messages()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['content'],rows[0]['status']),('heard','cancelled'))

    def test_relationship_daily_limit_and_role_isolation(self):
        self.db.add_character(replace(DEFAULT_CHARACTER,id='other',name='Other'))
        original=self.db.relationship('emilia')['trust']
        self.db.record_relationship_event('emilia'); self.db.record_relationship_event('emilia')
        self.assertEqual(self.db.relationship('emilia')['trust'],original+1)
        self.assertEqual(self.db.relationship('other')['trust'],original)

    def test_reminder_crud_and_due_only_once(self):
        self.db.save_reminder('emilia','first','2000-01-01 00:00:00')
        row=self.db.reminders('emilia')[0]
        self.db.save_reminder('emilia','updated','2000-01-01 00:00:00',row['id'])
        self.assertEqual(self.db.due_reminders('emilia')[0]['title'],'updated')
        self.assertEqual(self.db.due_reminders('emilia'),[])
        self.db.delete_reminder('emilia',row['id'])
        self.assertEqual(self.db.reminders('emilia'),[])

    def test_deleting_memory_clears_summary(self):
        self.db.save_summary('emilia','old summary',1)
        self.db.upsert_memory('private fact',fact_key='private')
        self.db.delete_memory(self.db.recent_memories()[0]['id'],'emilia')
        self.db.save_summary('emilia','must not resurrect',2)
        self.assertEqual(self.db.load_summary('emilia'),'')


class StreamTests(unittest.TestCase):
    def setUp(self): self.client=OpenAICompatibleClient('https://example.invalid','dummy','dummy')

    def stream(self,events):
        raw=''.join('data: '+(event if isinstance(event,str) else json.dumps(event))+'\n\n' for event in events).encode()
        with patch('aicompanion.llm.openai_compatible_client.urlopen',return_value=io.BytesIO(raw)):
            return list(self.client.generate_reply_stream([],'test'))

    def test_usage_and_null_are_valid(self):
        self.assertEqual(self.stream([{'choices':[{'delta':{'content':None}}]}, {'choices':[{'delta':{'content':'text'}}]},
                                     {'choices':[],'usage':{}},'[DONE]']),['text'])

    def test_finish_reason_without_done(self):
        self.assertEqual(self.stream([{'choices':[{'delta':{'content':'text'}}]}, {'choices':[{'delta':{},'finish_reason':'stop'}]}]),['text'])

    def test_eof_is_not_success(self):
        with self.assertRaises(LLMResponseError): self.stream([{'choices':[{'delta':{'content':'partial'}}]}])

    def test_empty_is_not_success(self):
        with self.assertRaises(LLMResponseError): self.stream(['[DONE]'])

    def test_invalid_types_are_normalized(self):
        for data in [{'choices':[{'delta':'bad'}]},[],{'choices':[{'delta':{'content':2}}]}, {'error':{'message':'bad'}}]:
            with self.subTest(data=data), self.assertRaises(LLMResponseError): self.client._parse_stream_token(json.dumps(data))

    def test_cancel_before_request(self):
        token=CancellationToken(); token.cancel()
        with self.assertRaises(ReplyCancelled): list(self.client.generate_reply_stream([],'test',token))


class CoordinationTests(unittest.TestCase):
    def test_burst_and_stale_events(self):
        time=[0]; c=ConversationCoordinator(clock=lambda:time[0])
        c.submit('one'); time[0]=.8; c.submit('two'); self.assertFalse(c.ready())
        time[0]=2.1; self.assertTrue(c.ready()); generation,query=c.begin()
        self.assertEqual(query,'one\ntwo'); self.assertTrue(c.accepts(generation))
        c.submit('three'); self.assertFalse(c.accepts(generation))

    def test_max_wait(self):
        time=[0]; c=ConversationCoordinator(clock=lambda:time[0]); c.submit('one')
        for n in (1,2,3,4): time[0]=n; c.submit(str(n))
        self.assertTrue(c.ready())

    def test_sentence_buffer(self):
        b=SentenceBuffer(); self.assertEqual(b.feed('今天'),[])
        self.assertEqual(b.feed('很好。明天'),['今天很好。'])
        self.assertEqual(b.feed('',True),['明天'])

    def test_silence_does_not_start_voice(self):
        detector=UtteranceDetector(); self.assertEqual(detector.feed(bytes(16000*2)),[])


if __name__=='__main__': unittest.main()
