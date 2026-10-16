"""Production service/controller with synthetic sources and resistant owned work."""
import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from app.device_hub import DeviceHub
from app.project_report import ProjectGrant,ReportStore,ReportPlanner,ReportWorker
from app.report_runtime import ReportRuntime
from app.stop_observer import StopObserver
from test_project_report import SyntheticPlanner


class BoundedStop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        source=self.root/'source';source.mkdir();(source/'README.md').write_text('Original synthetic bounded Stop fixture.\n')
        self.artifacts=self.root/'reports';self.artifacts.mkdir(mode=0o700)
        self.store=ReportStore(self.root/'tasks.db',[ProjectGrant('owner','demo',source,('README.md',))],self.artifacts)
        self.planner=ReportPlanner(self.store,SyntheticPlanner())
        task=self.store.create('owner','create','Prepare report','demo')
        task=await self.planner.propose('owner',task['id'])
        self.task=self.store.approve('owner',task['id'],'approve',task['revision'],task['scope_digest'],task['nonce'])
        self.runtime=ReportRuntime(self.store,self.planner,{'lcd':'owner','other':'other-owner'},DeviceHub(),lambda device:True,lambda device:True)
        await self.runtime.service.start()
        self.entered=asyncio.Event();self.release=asyncio.Event();self.tick=None
    async def asyncTearDown(self):
        self.release.set()
        if self.tick is not None:
            await asyncio.wait_for(asyncio.shield(self.tick),2)
        await self.runtime.stop_observer.close();await self.runtime.service.close()
        self.temp.cleanup()
    def token(self):
        return self.runtime.bridge.scene('lcd',self.task['id']).actions[0].id
    async def resistant(self,task):
        self.entered.set()
        while not self.release.is_set():
            try:await self.release.wait()
            except asyncio.CancelledError:pass
    async def start_resistant(self):
        factory=lambda store,owner:ReportWorker(store,owner,self.resistant)
        with patch('app.report_service.ReportWorker',side_effect=factory):
            self.tick=asyncio.create_task(self.runtime.service.tick())
            await asyncio.wait_for(self.entered.wait(),1)
    async def test_resistant_worker_revocation_is_confirmed_but_drain_is_not(self):
        await self.start_resistant();token=self.token()
        result=await asyncio.wait_for(self.runtime.stop('lcd',token),2)
        self.assertTrue(result.ack_confirmed);self.assertTrue(result.authority_revoked)
        self.assertFalse(result.handler_drained);self.assertEqual(result.state_at_ack,'stopping')
        self.assertFalse(self.tick.done());self.assertIsNotNone(self.runtime.service.fd)
        self.assertFalse(await ReportWorker(self.store,'owner').run(self.task['id']))
        again=await self.runtime.stop('lcd',token)
        self.assertEqual(again,result)  # Same action never replaces revocation authority.
        self.release.set();self.assertTrue(await self.tick)
        saved=self.store.get('owner',self.task['id'])
        self.assertEqual(saved['state'],'stopped');self.assertIsNone(saved['artifact'])
        self.assertEqual(list(self.artifacts.iterdir()),[])
        self.assertIsNotNone(self.runtime.service.fd)  # Child cancellation did not kill supervisor.
    async def test_persistence_timeout_is_unconfirmed_and_late_commit_is_retained(self):
        await self.start_resistant();token=self.token();permit=asyncio.Event()
        self.runtime.stop_observer=StopObserver(ack_budget=.02,drain_grace=.01)
        original=self.store.io
        async def delayed(operation,*args,**kwargs):
            if getattr(operation,'__name__','')=='stop':await permit.wait()
            return await original(operation,*args,**kwargs)
        with patch.object(self.store,'io',side_effect=delayed):
            result=await asyncio.wait_for(self.runtime.stop('lcd',token),.5)
            self.assertFalse(result.ack_confirmed);self.assertFalse(result.authority_revoked)
            self.assertEqual(result.code,'stop_not_confirmed')
            self.assertEqual(self.store.get('owner',self.task['id'])['state'],'running')
            permit.set()
            persistence,observation,_=next(iter(self.runtime.stop_observer.attempts.values()))
            later=await asyncio.wait_for(asyncio.shield(observation),1)
            self.assertTrue(later.authority_revoked);self.assertFalse(later.handler_drained)
        self.release.set();await self.tick
        self.assertEqual(self.store.get('owner',self.task['id'])['state'],'stopped')
    async def test_cancelled_request_does_not_cancel_and_forget_revocation(self):
        await self.start_resistant();token=self.token();permit=asyncio.Event();entered=asyncio.Event()
        original=self.store.io
        async def delayed(operation,*args,**kwargs):
            if getattr(operation,'__name__','')=='stop':entered.set();await permit.wait()
            return await original(operation,*args,**kwargs)
        with patch.object(self.store,'io',side_effect=delayed):
            request=asyncio.create_task(self.runtime.stop('lcd',token))
            await entered.wait();request.cancel()
            with self.assertRaises(asyncio.CancelledError):await request
            self.assertTrue(self.runtime.stop_observer.attempts)
            permit.set();_,observation,_=next(iter(self.runtime.stop_observer.attempts.values()))
            result=await asyncio.wait_for(asyncio.shield(observation),1)
            self.assertTrue(result.authority_revoked)
        self.release.set();await self.tick
    async def test_storage_failure_does_not_claim_stop_or_signal_handler(self):
        await self.start_resistant();token=self.token();original=self.store.io
        async def failed(operation,*args,**kwargs):
            if getattr(operation,'__name__','')=='stop':raise OSError('synthetic private storage failure')
            return await original(operation,*args,**kwargs)
        with patch.object(self.store,'io',side_effect=failed):
            result=await self.runtime.stop('lcd',token)
            self.assertFalse(result.ack_confirmed);self.assertFalse(result.authority_revoked)
            self.assertNotIn('private storage',result.message)
        self.assertEqual(self.store.get('owner',self.task['id'])['state'],'running')
        self.release.set();await self.tick
    async def test_stale_generation_and_other_owner_cannot_revoke(self):
        token=self.token();grant=self.runtime.bridge.stop_grant('lcd',token)
        with self.assertRaises(ValueError):await self.runtime.stop('other',token)
        changed=self.store.stop('owner',self.task['id'],'separate-stop')
        with self.assertRaises(ValueError):
            self.store.stop('owner',self.task['id'],'stale-stop',expected_generation=grant['generation'])
        self.assertEqual(self.store.get('owner',self.task['id'])['generation'],changed['generation'])
    async def test_admitted_native_write_remains_quarantined_until_real_drain(self):
        import app.project_report as report
        entered,release=threading.Event(),threading.Event();original=report.os.fsync;first=True
        def held(fd):
            nonlocal first
            if first:
                first=False;entered.set()
                if not release.wait(3):raise TimeoutError('Synthetic safety release timed out')
            return original(fd)
        with patch.object(report.os,'fsync',held):
            self.tick=asyncio.create_task(self.runtime.service.tick())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                result=await self.runtime.stop('lcd',self.token())
                self.assertTrue(result.authority_revoked);self.assertFalse(result.handler_drained)
                self.assertIsNone(self.store.get('owner',self.task['id'])['artifact'])
            finally:release.set()
            await asyncio.wait_for(asyncio.shield(self.tick),2)
        saved=self.store.get('owner',self.task['id'])
        self.assertEqual(saved['state'],'stopped');self.assertIsNone(saved['artifact'])
        self.assertEqual(saved['write_intent']['state'],'may_exist')
        self.assertTrue((self.artifacts/(self.task['id']+'.md')).exists())

    async def test_pending_stop_blocks_same_device_new_work_but_not_status_recovery(self):
        await self.start_resistant();token=self.token()
        await self.runtime.stop('lcd',token)
        self.assertTrue(await self.runtime.mutation_blocked('lcd'))
        self.assertFalse(await self.runtime.mutation_blocked('not-enrolled'))
        from app import main
        from app.report_runtime import ReportStopPending
        with patch.object(main,'report_runtime',self.runtime), patch.object(main.job_store,'enqueue',side_effect=AssertionError('No new generic work may be admitted')):
            with self.assertRaises(ReportStopPending):
                await main.handle_utterance('lcd','set a timer for 2 minutes','blocked-stop-request')
        # Existing read-only task lookup remains available; no clearing/retry is offered.
        status=await self.store.io(self.store.get,'owner',self.task['id'])
        self.assertEqual(status['state'],'stopping')
        self.assertEqual(self.runtime.bridge.scene('lcd',self.task['id']).title,'Project report')
        self.release.set();await self.tick
        self.assertFalse(await self.runtime.mutation_blocked('lcd'))

    async def test_restart_without_drain_proof_retains_explicit_device_quarantine(self):
        claimed=self.store.claim('owner',self.task['id'],'synthetic-lost-worker')
        self.assertIsNotNone(claimed)
        token=self.token();self.runtime.bridge.stop('lcd',token)
        with self.store.db() as db:
            db.execute('INSERT INTO report_device_views VALUES(?,?,?)',('lcd','owner',self.task['id']))
        # No actual worker exists in this simulated lost-process fixture.
        self.store.recover('owner')
        restored=ReportRuntime(self.store,self.planner,{'lcd':'owner','another-device':'owner'},DeviceHub(),lambda device:True,lambda device:True)
        self.assertTrue(await restored.mutation_blocked('lcd'))
        other=self.store.create('owner','another-task','Prepare another report','demo')
        with self.store.db() as db:
            db.execute('UPDATE report_device_views SET task=? WHERE device=?',(other['id'],'lcd'))
            binding=db.execute('SELECT generation FROM report_device_stops WHERE device=? AND task=?',('lcd',self.task['id'])).fetchone()
        self.assertIsNotNone(binding)
        self.assertTrue(await restored.mutation_blocked('lcd'))
        self.assertFalse(await restored.mutation_blocked('another-device'))
        self.assertEqual(self.store.get('owner',self.task['id'])['state'],'uncertain')
        self.assertFalse(await ReportWorker(self.store,'owner').run(self.task['id']))
