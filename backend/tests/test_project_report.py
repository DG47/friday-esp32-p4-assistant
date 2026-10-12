import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.project_report import ProjectGrant, ReportStore, ReportPlanner, ReportWorker
from app.providers.base import ProviderTurn, ToolCall
from app.report_telegram import ReportTelegramAdapter, ReportTelegramTransport
from app.report_service import ReportService
from test_task_telegram_transport import FakeHTTP

class SyntheticPlanner:
    local_only=True;name='original-synthetic-planner'
    def __init__(self,proposal=None):self.calls=0;self.proposal=proposal or {'capability':'project-report-v1','sections':['inventory','readme','source_statistics']}
    async def complete(self,*args):
        self.calls+=1
        return ProviderTurn(tool_calls=[ToolCall('original-plan','propose_project_report',self.proposal)])

class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.source=self.root/'source';self.source.mkdir();(self.source/'src').mkdir()
        (self.source/'README.md').write_text('Original synthetic project.\nNo personal content.\n')
        (self.source/'src'/'sample.py').write_text('def original_example():\n    return 17\n')
        self.output=self.root/'reports';self.output.mkdir(mode=0o700)
        self.grant=ProjectGrant('owner','demo',self.source,('README.md','src/sample.py'))
        self.store=ReportStore(self.root/'tasks.db',[self.grant],self.output)
        self.provider=SyntheticPlanner();self.planner=ReportPlanner(self.store,self.provider)
        self.adapter=ReportTelegramAdapter(self.store);self.http=FakeHTTP();self.transport=ReportTelegramTransport(self.adapter,self.http,enabled=True)
        await self.transport.bind_verified(100,100,'owner')
    async def asyncTearDown(self):self.temp.cleanup()
    def update(self,number,text):return {'update_id':number,'message':{'from':{'id':100,'is_bot':False},'chat':{'id':100,'type':'private'},'text':text}}
    async def task(self):
        task=self.store.create('owner','original-create','Prepare a project report','demo')
        return await self.planner.propose('owner',task['id'])
    def approve(self,task):return self.store.approve('owner',task['id'],'original-approve',task['revision'],task['scope_digest'],task['nonce'])
    async def test_actual_read_report_sha_and_source_unchanged(self):
        task=await self.task();self.assertEqual(task['state'],'awaiting_approval')
        self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']));self.assertEqual(list(self.output.iterdir()),[])
        source_before=self.grant.manifest();self.approve(task)
        with patch('subprocess.Popen',side_effect=AssertionError('No process execution')):
            self.assertTrue(await ReportWorker(self.store,'owner').run(task['id']))
        result=self.store.get('owner',task['id']);artifact=result['artifact'];data=(self.output/artifact['name']).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(),artifact['sha256']);self.assertIn(b'Original synthetic project',data)
        self.assertIn(b'src/sample.py: 2 lines',data);self.assertEqual(source_before,self.grant.manifest())
        self.assertEqual(result['state'],'ready_for_review');self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']))
    async def test_invalid_plan_and_unsupported_do_not_execute(self):
        for n,proposal in enumerate([{'capability':'project-report-v1','sections':['shell']},{'capability':'project-report-v1','sections':['inventory'],'path':'/etc'},{'capability':'unsupported','sections':[]} ]):
            task=self.store.create('owner','unsupported-'+str(n),'Prepare project report','demo')
            result=await ReportPlanner(self.store,SyntheticPlanner(proposal)).propose('owner',task['id'])
            self.assertEqual(result['state'],'failed');self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']))
        self.assertEqual(list(self.output.iterdir()),[])
    async def test_project_and_owner_binding(self):
        with self.assertRaises(ValueError):self.store.create('other','cross-owner','Prepare report','demo')
        with self.assertRaises(ValueError):self.store.create('owner','path','Prepare report','../../source')
        task=await self.task()
        with self.assertRaises(ValueError):self.store.get('other',task['id'])
        with self.assertRaises(ValueError):self.store.conversation('other',task['id'],'turn','LCD','status')
    async def test_changed_source_requires_fresh_scope(self):
        task=await self.task();self.approve(task);(self.source/'README.md').write_text('Original changed synthetic source\n')
        self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']))
        current=self.store.get('owner',task['id']);self.assertEqual(current['state'],'awaiting_approval');self.assertNotEqual(current['scope_digest'],task['scope_digest'])
        with self.assertRaises(ValueError):self.store.approve('owner',task['id'],'old-scope',task['revision'],task['scope_digest'],task['nonce'])
    async def test_stop_during_execution_drains_no_false_completion(self):
        task=await self.task();self.approve(task);entered=asyncio.Event();release=asyncio.Event()
        async def paused(_):entered.set();await release.wait()
        runner=asyncio.create_task(ReportWorker(self.store,'owner',paused).run(task['id']));await entered.wait()
        state=self.store.stop('owner',task['id'],'stop-original');self.assertEqual(state['state'],'stopping')
        release.set();self.assertFalse(await runner);self.assertEqual(self.store.get('owner',task['id'])['state'],'stopped');self.assertEqual(list(self.output.iterdir()),[])
    async def test_async_cancel_drains_before_stopped(self):
        task=await self.task();self.approve(task);entered=asyncio.Event()
        async def paused(_):entered.set();await asyncio.Event().wait()
        runner=asyncio.create_task(ReportWorker(self.store,'owner',paused).run(task['id']));await entered.wait();runner.cancel()
        with self.assertRaises(asyncio.CancelledError):await runner
        self.assertEqual(self.store.get('owner',task['id'])['state'],'stopped')
    async def test_restart_write_intent_uncertain_no_replay(self):
        task=await self.task();self.approve(task);admitted=self.store.claim('owner',task['id'],'original-worker')
        self.store.write_intent('owner',admitted,task['id']+'.md','1'*64)
        (self.output/(task['id']+'.md')).write_text('Original synthetic partial report')
        self.store.recover('owner');state=self.store.get('owner',task['id']);self.assertEqual(state['state'],'uncertain')
        self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']));self.assertFalse(self.store.finish('owner',admitted,{'name':'incorrect'}))
        self.assertEqual((self.output/(task['id']+'.md')).read_text(),'Original synthetic partial report')
    async def test_no_overwrite_existing_artifact(self):
        task=await self.task();self.approve(task);path=self.output/(task['id']+'.md');path.write_text('Original existing bytes')
        self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']));self.assertEqual(path.read_text(),'Original existing bytes');self.assertEqual(self.store.get('owner',task['id'])['state'],'uncertain')
    async def test_cross_surface_same_revision_and_verified_result(self):
        task=await self.task();self.approve(task);await ReportWorker(self.store,'owner').run(task['id'])
        first=self.store.conversation('owner',task['id'],'lcd-turn','LCD','What is its status?')
        second=self.store.conversation('owner',task['id'],'telegram-turn','Telegram','What is its status?')
        self.assertEqual(first,second);self.assertIn('ready_for_review',first);self.assertEqual(len(self.store.turns('owner',task['id'])),2)
        with self.assertRaises(ValueError):self.store.conversation('owner',task['id'],'lcd-turn','LCD','Different message')
    async def test_typed_scope_full_delivery_then_callback_and_notification(self):
        await self.transport.process_update(self.update(1,'/report demo Prepare a read-only project report'))
        tasks=self.store.list('owner');self.assertEqual(len(tasks),1);task=tasks[0]
        await self.planner.propose('owner',task['id']);self.assertTrue(await self.transport.notify('owner',task['id']));self.assertFalse(await self.transport.notify('owner',task['id']))
        with self.store.db(write=False) as db:self.assertEqual(db.execute('SELECT offset FROM telegram_transport_meta').fetchone()[0],2)
        while await self.transport.deliver_once():pass
        keyboard=next(payload for method,payload in self.http.calls if 'reply_markup' in payload)
        token=keyboard['reply_markup']['inline_keyboard'][0][0]['callback_data']
        callback={'update_id':2,'callback_query':{'id':'original-callback','from':{'id':100,'is_bot':False},'message':{'chat':{'id':100,'type':'private'}},'data':token}}
        await self.transport.process_update(callback);self.assertEqual(self.store.get('owner',task['id'])['state'],'queued')
        self.assertTrue(await ReportWorker(self.store,'owner').run(task['id']));await self.transport.notify('owner',task['id'])
        while await self.transport.deliver_once():pass
        sent='\n'.join(payload.get('text','') for method,payload in self.http.calls)
        self.assertIn('Verified report SHA256',sent);self.assertNotIn('No personal content',sent)
        await self.transport.process_update(self.update(1,'/report demo Prepare a read-only project report'));self.assertEqual(len(self.store.list('owner')),1)
    async def test_revoked_binding_no_notification_delivery(self):
        response=self.adapter.handle(self.update(1,'/report demo Prepare a report'));task=self.store.list('owner')[0]
        await self.planner.propose('owner',task['id']);await self.transport.revoke(100)
        with self.assertRaises(ValueError):await self.transport.notify('owner',task['id'])
        self.assertEqual(self.http.calls,[])
    async def test_uncertain_notification_not_resend_on_recover(self):
        self.adapter.handle(self.update(1,'/report demo Prepare a report'));task=self.store.list('owner')[0];await self.planner.propose('owner',task['id']);await self.transport.notify('owner',task['id'])
        self.http.replies=[TimeoutError('Original synthetic timeout')]
        with self.assertRaises(TimeoutError):await self.transport.deliver_once()
        await self.transport.recover()
        while await self.transport.deliver_once():pass
        self.assertEqual(len(self.http.calls),1)
        with self.store.db(write=False) as db:self.assertTrue(db.execute("SELECT COUNT(*) FROM telegram_outbox WHERE status='uncertain'").fetchone()[0])
    async def test_supervisor_disabled_and_single_ownership(self):
        service=ReportService(self.store,self.planner,self.transport)
        with self.assertRaises(ValueError):await service.start()
        first=ReportService(self.store,self.planner,self.transport,enabled=True);second=ReportService(self.store,self.planner,self.transport,enabled=True)
        await first.start()
        try:
            with self.assertRaises(BlockingIOError):await second.start()
        finally:await first.close()
    async def test_planner_cancelled_and_restart_no_automatic_model_retry(self):
        entered=asyncio.Event()
        class Blocked(SyntheticPlanner):
            async def complete(inner,*args):inner.calls+=1;entered.set();await asyncio.Event().wait()
        provider=Blocked();task=self.store.create('owner','planning-cancel','Prepare report','demo')
        run=asyncio.create_task(ReportPlanner(self.store,provider).propose('owner',task['id']));await entered.wait();run.cancel()
        with self.assertRaises(asyncio.CancelledError):await run
        self.assertEqual(self.store.get('owner',task['id'])['state'],'uncertain')
        await ReportPlanner(self.store,provider).propose('owner',task['id']);self.assertEqual(provider.calls,1)
    async def test_path_denials_and_size(self):
        for path in ['../README.md','/README.md','.env','secret.md','src/credentials.json']:
            with self.assertRaises(ValueError):ProjectGrant('owner','bad',self.source,(path,))
        (self.source/'link.md').symlink_to(self.source/'README.md')
        with self.assertRaises(OSError):ProjectGrant('owner','links',self.source,('link.md',)).manifest()
        os.link(self.source/'README.md',self.source/'hard.md')
        with self.assertRaises(ValueError):self.grant.manifest()
        os.unlink(self.source/'hard.md')
        (self.source/'big.txt').write_bytes(b'x'*65537)
        with self.assertRaises(ValueError):ProjectGrant('owner','big',self.source,('big.txt',)).manifest()
        os.mkfifo(self.source/'pipe.txt')
        with self.assertRaises(ValueError):ProjectGrant('owner','pipe',self.source,('pipe.txt',)).manifest()

    async def test_source_deleted_after_approval_does_not_kill_supervisor(self):
        task=await self.task();self.approve(task);(self.source/'README.md').unlink()
        service=ReportService(self.store,self.planner,self.transport,enabled=True);await service.start()
        try:
            self.assertTrue(await service.tick())
            self.assertEqual(self.store.get('owner',task['id'])['state'],'failed')
            self.assertFalse(await service.tick())
        finally:await service.close()

    async def test_local_task_without_telegram_binding_is_supported(self):
        self.store.create('owner','local-task','Prepare report','demo')
        service=ReportService(self.store,self.planner,self.transport,enabled=True);await service.start()
        try:
            self.assertTrue(await service.tick());self.assertEqual(self.http.calls,[])
        finally:await service.close()

    async def test_planning_stop_revokes_authority_without_drain_claim(self):
        entered=asyncio.Event();release=asyncio.Event()
        class Delayed(SyntheticPlanner):
            async def complete(inner,*args):
                entered.set();await release.wait();return await super(Delayed,inner).complete(*args)
        task=self.store.create('owner','delayed-plan','Prepare report','demo')
        run=asyncio.create_task(ReportPlanner(self.store,Delayed()).propose('owner',task['id']));await entered.wait()
        self.assertEqual(self.store.stop('owner',task['id'],'planning-stop')['state'],'uncertain')
        release.set();result=await run
        self.assertEqual(result['state'],'uncertain');self.assertIsNone(result['scope']);self.assertEqual(list(self.output.iterdir()),[])

    async def test_unsafe_goal_refused_even_if_model_would_allow(self):
        task=self.store.create('owner','unsafe','Delete project files and send a report','demo')
        result=await self.planner.propose('owner',task['id'])
        self.assertEqual(result['state'],'failed');self.assertEqual(self.provider.calls,0)

    async def test_nonprivate_artifact_root_refused(self):
        self.output.chmod(0o755)
        with self.assertRaises(ValueError):ReportStore(self.root/'other.db',[self.grant],self.output)

    async def test_native_projection_owner_bound_stop_and_shared_context(self):
        from app.report_context import ReportContext
        task=await self.task();bridge=ReportContext(self.store,{'lcd':'owner','other-lcd':'other'})
        scene=bridge.scene('lcd',task['id']);token=scene.actions[0].id
        with self.assertRaises(ValueError):bridge.scene('other-lcd',task['id'])
        with self.assertRaises(ValueError):bridge.stop('other-lcd',token)
        answer=bridge.ask('lcd',task['id'],'lcd-original','status')
        self.assertEqual(answer,self.store.conversation('owner',task['id'],'phone-original','Telegram','status'))
        self.assertEqual(bridge.stop('lcd',token)['state'],'stopped')
        with self.assertRaises(ValueError):bridge.stop('lcd',token)

    async def test_stop_during_admitted_file_write_does_not_reverse_bytes(self):
        import threading
        import app.project_report as module
        task=await self.task();self.approve(task);entered=threading.Event();release=threading.Event();original=module.os.fsync
        def paused(fd):
            entered.set()
            if not release.wait(3):raise TimeoutError('Original bounded fixture wait')
            return original(fd)
        with patch.object(module.os,'fsync',side_effect=paused):
            run=asyncio.create_task(ReportWorker(self.store,'owner').run(task['id']))
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            self.assertEqual(self.store.stop('owner',task['id'],'stop-during-write')['state'],'stopping')
            self.assertFalse(run.done());release.set();self.assertFalse(await run)
        current=self.store.get('owner',task['id']);self.assertEqual(current['state'],'stopped');self.assertIsNone(current['artifact'])
        self.assertTrue((self.output/(task['id']+'.md')).exists());self.assertEqual(current['write_intent']['state'],'may_exist')

    async def test_notification_capacity_keeps_revision_retryable(self):
        self.adapter.handle(self.update(1,'/report demo Prepare a report'));task=self.store.list('owner')[0];await self.planner.propose('owner',task['id'])
        self.transport.ordinary_limit=0
        with self.assertRaises(ValueError):await self.transport.notify('owner',task['id'])
        with self.store.db(write=False) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM report_notifications').fetchone()[0],0)
        self.transport.ordinary_limit=5000;self.assertTrue(await self.transport.notify('owner',task['id']))

    def test_model_context_bound_configuration(self):
        from app.providers.ollama_provider import OllamaProvider
        for value in [True,0,10000,'8192']:
            with self.assertRaises(ValueError):OllamaProvider('http://127.0.0.1:11434','synthetic:local',local_only=True,context_tokens=value)
        self.assertEqual(OllamaProvider('http://127.0.0.1:11434','synthetic:local',local_only=True,context_tokens=8192).context_tokens,8192)

    async def test_stop_before_planning_never_delegates_two_transaction_gap(self):
        from app.task_slice import TaskStore
        task=self.store.create('owner','stop-before-plan','Prepare report','demo')
        with patch.object(TaskStore,'stop',side_effect=AssertionError('Stop must be atomic in ReportStore')):
            self.assertEqual(self.store.stop('owner',task['id'],'atomic-stop')['state'],'stopped')
        await self.planner.propose('owner',task['id']);self.assertEqual(self.provider.calls,0)

    async def test_artifact_permission_drift_rejected_before_write(self):
        task=await self.task();self.approve(task);self.output.chmod(0o755)
        self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']))
        self.assertEqual(list(self.output.iterdir()),[]);self.assertEqual(self.store.get('owner',task['id'])['state'],'uncertain')

    async def test_explicit_service_loop_polls_and_drains_with_mock_only(self):
        service=ReportService(self.store,self.planner,self.transport,enabled=True);stop=asyncio.Event()
        run=asyncio.create_task(service.run(stop))
        for _ in range(20):
            if any(method=='getUpdates' for method,_ in self.http.calls):break
            await asyncio.sleep(.01)
        self.assertTrue(any(method=='getUpdates' for method,_ in self.http.calls))
        stop.set();await asyncio.wait_for(run,2);self.assertIsNone(service.fd)

    async def test_stop_transaction_serializes_concurrent_planning_admission(self):
        import threading
        task=self.store.create('owner','planning-race','Prepare report','demo')
        entered=threading.Event();release=threading.Event();plan_entered=threading.Event()
        action=self.store._action;begin=self.store.begin_plan
        def paused_action(db,owner,action_id,payload,task_id):
            if payload['op']=='stop':
                entered.set()
                if not release.wait(2):raise TimeoutError('Original synthetic Stop gate')
            return action(db,owner,action_id,payload,task_id)
        def observed_begin(*args):plan_entered.set();return begin(*args)
        with patch.object(self.store,'_action',side_effect=paused_action), patch.object(self.store,'begin_plan',side_effect=observed_begin):
            stop=asyncio.create_task(self.store.io(self.store.stop,'owner',task['id'],'atomic-race-stop'))
            self.assertTrue(await asyncio.to_thread(entered.wait,1))
            planning=asyncio.create_task(self.planner.propose('owner',task['id']))
            self.assertTrue(await asyncio.to_thread(plan_entered.wait,1));self.assertFalse(planning.done())
            release.set();self.assertEqual((await stop)['state'],'stopped');self.assertEqual((await planning)['state'],'stopped')
        self.assertEqual(self.provider.calls,0)

    async def test_verified_result_and_shared_context_survive_store_recreation(self):
        task=await self.task();self.approve(task);await ReportWorker(self.store,'owner').run(task['id'])
        before=self.store.conversation('owner',task['id'],'original-before-restart','Telegram','status')
        reopened=ReportStore(self.root/'tasks.db',[self.grant],self.output);reopened.recover('owner')
        self.assertEqual(reopened.get('owner',task['id'])['artifact'],self.store.get('owner',task['id'])['artifact'])
        self.assertEqual(reopened.conversation('owner',task['id'],'original-after-restart','LCD','status'),before)
        self.assertFalse(await ReportWorker(reopened,'owner').run(task['id']))

    async def test_untrusted_readme_is_inert_markdown_code_excerpt(self):
        (self.source/'README.md').write_text('```\n<img src="https://example.invalid/original.png">\n```\nOriginal synthetic untrusted text')
        task=await self.task();self.approve(task);await ReportWorker(self.store,'owner').run(task['id'])
        data=(self.output/(task['id']+'.md')).read_text()
        self.assertIn('````text\n```',data);self.assertTrue(data.rstrip().endswith('````'))

    async def test_changed_storage_executor_requires_new_approval(self):
        task=await self.task();self.approve(task)
        with patch('app.task_slice.EXECUTOR_DIGEST','f'*64):
            self.assertFalse(await ReportWorker(self.store,'owner').run(task['id']))
            current=self.store.get('owner',task['id']);self.assertEqual(current['state'],'awaiting_approval')
            self.assertNotEqual(current['scope_digest'],task['scope_digest']);self.assertNotEqual(current['nonce'],task['nonce'])
        self.assertEqual(list(self.output.iterdir()),[])

    async def test_async_factory_uses_report_adapter_and_mock_only(self):
        transport=await ReportTelegramTransport.create(self.store,self.http,enabled=True)
        self.assertIsInstance(transport.adapter,ReportTelegramAdapter)
        with self.assertRaises(ValueError):await ReportTelegramTransport.create(self.store,object(),enabled=True)

    async def test_nonreport_goal_refused_without_model_or_scope(self):
        task=self.store.create('owner','flight-goal','Reserve a flight for tomorrow.','demo')
        result=await self.planner.propose('owner',task['id'])
        self.assertEqual(result['state'],'failed');self.assertEqual(self.provider.calls,0)
        review=self.adapter._review('owner',100,100,result)
        self.assertIn('only enrolled read-only project reports',review['text'].lower());self.assertEqual(review['buttons'],[])
