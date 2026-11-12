import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.task_slice import TaskStore,TaskWorker,STEPS
from app.task_local import LocalTaskStore
from app.task_planner import TaskPlanner
from app.providers.base import TextProvider,ProviderTurn,ToolCall
from app.providers.router import ProviderRouter


def turn(**changes):
    proposal={'repair':'inclusive-range-v1','operations':list(STEPS),'evidence_refs':[]}
    proposal.update(changes)
    return ProviderTurn(tool_calls=[ToolCall('call','propose_registered_plan',proposal)])


class FakeProvider(TextProvider):
    name='injected-test-provider'
    def __init__(self,result=None,gate=None):self.result=result or turn();self.calls=[];self.gate=gate;self.cancelled=False
    async def complete(self,messages,instructions,tools):
        self.calls.append((messages,instructions,tools))
        try:
            if self.gate:await self.gate.wait()
            return self.result
        except asyncio.CancelledError:self.cancelled=True;raise


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=TaskStore(Path(self.temp.name)/'tasks.db')
        self.provider=FakeProvider()
        self.planner=TaskPlanner(self.store,provider=self.provider,enabled=True)
        self.task=self.store.create('owner','start','Diagnose the original fixture','range-total')
    def tearDown(self):self.temp.cleanup()

    async def test_off_by_default_no_provider_call(self):
        planner=TaskPlanner(self.store,provider=self.provider)
        with self.assertRaises(ValueError):await planner.propose('owner',self.task['id'],'plan')
        self.assertEqual(self.provider.calls,[])

    async def test_existing_router_injected_valid_plan_fresh_scope(self):
        # Exercise the existing router interface without selecting any live SDK.
        router=ProviderRouter(type('Settings',(),{'assistant_provider':'mock'})())
        planner=TaskPlanner(self.store,router=router,enabled=True)
        with patch.object(router,'select',return_value=self.provider) as select:
            result=await planner.propose('owner',self.task['id'],'plan')
        select.assert_called_once()
        self.assertEqual(result['state'],'ready_for_approval')
        fresh=self.store.get('owner',self.task['id'])
        self.assertEqual(fresh['state'],'awaiting_approval');self.assertFalse(fresh['scope']['commands'])
        self.assertEqual(fresh['scope']['operations'],list(STEPS))
        self.assertNotEqual(fresh['nonce'],self.task['nonce'])
        with self.assertRaises(ValueError):self.store.approve('owner',self.task['id'],'stale',self.task['revision'],self.task['scope_digest'],self.task['nonce'])
        self.store.approve('owner',fresh['id'],'approve',fresh['revision'],fresh['scope_digest'],fresh['nonce'])
        await TaskWorker(self.store,'owner').run(fresh['id'])
        self.assertEqual(self.store.get('owner',fresh['id'])['state'],'ready_for_review')

    async def test_duplicate_planning_no_second_inference(self):
        result=await self.planner.propose('owner',self.task['id'],'plan')
        self.assertEqual(await self.planner.propose('owner',self.task['id'],'plan'),result)
        self.assertEqual(len(self.provider.calls),1)
        other=self.store.create('owner','second','Other fixture','range-total')
        with self.assertRaises(ValueError):await self.planner.propose('owner',other['id'],'plan')

    async def test_malformed_fabricated_or_expanded_results_denied(self):
        results=[turn(commands=['rm']),turn(repair='custom-code'),turn(operations=['verify']),turn(evidence_refs=['invented']),
                 ProviderTurn(text='All tests passed',tool_calls=turn().tool_calls),ProviderTurn(),
                 ProviderTurn(tool_calls=[ToolCall('x','execute',{})])]
        for index,result in enumerate(results):
            self.provider.result=result
            self.assertEqual((await self.planner.propose('owner',self.task['id'],'bad'+str(index)))['state'],'failed')
            current=self.store.get('owner',self.task['id'])
            self.assertEqual(current['revision'],self.task['revision']);self.assertEqual(current['evidence'],[])

    async def test_stop_during_provider_call_discards_reply(self):
        self.provider.gate=asyncio.Event()
        planning=asyncio.create_task(self.planner.propose('owner',self.task['id'],'plan'))
        await asyncio.sleep(.02);self.store.stop('owner',self.task['id'],'stop')
        result=await planning
        self.assertEqual(result['state'],'cancelled');self.assertTrue(self.provider.cancelled)
        self.assertEqual(self.store.get('owner',self.task['id'])['state'],'stopped')

    async def test_review_change_during_planning_rejects_stale_response(self):
        self.provider.gate=asyncio.Event()
        pending=asyncio.create_task(self.planner.propose('owner',self.task['id'],'plan'))
        await asyncio.sleep(.02)
        refreshed=self.store.review('owner',self.task['id'],'review',self.task['revision'])
        self.provider.gate.set()
        result=await pending
        self.assertIn(result['state'],{'cancelled','failed'})
        self.assertEqual(self.store.get('owner',self.task['id'])['scope_digest'],refreshed['scope_digest'])

    async def test_recovery_fences_late_reply_and_does_not_retry(self):
        self.provider.gate=asyncio.Event()
        pending=asyncio.create_task(self.planner.propose('owner',self.task['id'],'plan'))
        await asyncio.sleep(.02);TaskPlanner(self.store).recover('owner');self.provider.gate.set()
        self.assertEqual((await pending)['state'],'uncertain')
        self.assertEqual((await self.planner.propose('owner',self.task['id'],'plan'))['state'],'uncertain')
        self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(self.store.get('owner',self.task['id'])['revision'],self.task['revision'])

    async def test_provider_absent_does_not_claim_real_reasoning(self):
        planner=TaskPlanner(self.store,enabled=True)
        self.assertEqual((await planner.propose('owner',self.task['id'],'absent'))['state'],'failed')

    async def test_local_executor_capabilities_unchanged(self):
        store=LocalTaskStore(Path(self.temp.name)/'local.db',Path(self.temp.name).resolve()/'projects')
        task=store.create('owner','start','Infer repair','range-total');before=copy.deepcopy(task['scope'])
        result=await TaskPlanner(store,provider=self.provider,enabled=True).propose('owner',task['id'],'plan')
        self.assertEqual(result['state'],'ready_for_approval')
        fresh=store.get('owner',task['id'])
        self.assertEqual({k:v for k,v in fresh['scope'].items() if k!='planner'},{k:v for k,v in before.items() if k!='planner'})
        self.assertFalse(store.root.joinpath(task['id']).exists())

    async def test_explicit_cancel_does_not_mutate_task_scope(self):
        self.provider.gate=asyncio.Event()
        pending=asyncio.create_task(self.planner.propose('owner',self.task['id'],'plan'))
        await asyncio.sleep(.02);pending.cancel()
        with self.assertRaises(asyncio.CancelledError):await pending
        self.assertEqual(self.planner.get('owner','plan')['state'],'cancelled')
        self.assertEqual(self.store.get('owner',self.task['id'])['revision'],self.task['revision'])

    async def test_deadline_uncertain_no_automatic_retry(self):
        self.provider.gate=asyncio.Event()
        planner=TaskPlanner(self.store,provider=self.provider,enabled=True,timeout=.02)
        result=await planner.propose('owner',self.task['id'],'plan')
        self.assertEqual(result['state'],'uncertain');self.assertTrue(self.provider.cancelled)
        self.assertEqual(await planner.propose('owner',self.task['id'],'plan'),result)
        self.assertEqual(len(self.provider.calls),1)

    async def test_planning_admission_writer_wait_keeps_event_loop_responsive(self):
        import sqlite3
        import threading
        import time
        ready = threading.Event()
        def writer():
            db = sqlite3.connect(self.store.path)
            try:
                db.execute('BEGIN IMMEDIATE')
                ready.set()
                time.sleep(.25)
                db.commit()
            finally:
                db.close()
        thread = threading.Thread(target=writer)
        thread.start()
        await asyncio.to_thread(ready.wait)
        pending = asyncio.create_task(self.planner.propose('owner', self.task['id'], 'blocked-plan'))
        start = asyncio.get_running_loop().time()
        await asyncio.sleep(.03)
        self.assertLess(asyncio.get_running_loop().time() - start, .15)
        self.assertFalse(pending.done())
        self.assertEqual((await pending)['state'], 'ready_for_approval')
        await asyncio.to_thread(thread.join)

    async def test_planning_waits_for_change_without_repeated_reads(self):
        self.provider.gate = asyncio.Event()
        with patch.object(self.store, 'get', wraps=self.store.get) as get:
            pending = asyncio.create_task(self.planner.propose('owner', self.task['id'], 'event-plan'))
            await asyncio.sleep(.15)
            self.assertLessEqual(get.call_count, 3)
            await self.store.io(self.store.stop, 'owner', self.task['id'], 'stop-plan')
            self.assertEqual((await pending)['state'], 'cancelled')

    async def test_cancel_during_admission_does_not_call_provider(self):
        import sqlite3
        import threading
        import time
        ready = threading.Event()
        def writer():
            db = sqlite3.connect(self.store.path)
            try:
                db.execute('BEGIN IMMEDIATE')
                ready.set()
                time.sleep(.2)
                db.commit()
            finally:
                db.close()
        thread = threading.Thread(target=writer)
        thread.start()
        await asyncio.to_thread(ready.wait)
        pending = asyncio.create_task(self.planner.propose('owner', self.task['id'], 'admission-cancel'))
        await asyncio.sleep(.02)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError): await pending
        await asyncio.to_thread(thread.join)
        self.assertEqual(self.provider.calls, [])
        try: plan = await self.store.io(self.planner.get, 'owner', 'admission-cancel')
        except ValueError: return  # Cancelled before transaction admission.
        self.assertEqual(plan['state'], 'cancelled')
