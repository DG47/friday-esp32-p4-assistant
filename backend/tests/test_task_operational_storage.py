import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from app.task_slice import TaskStore
from app.task_local import LocalTaskStore,LocalTaskWorker,AFTER


class StorageOperationalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(Path(__file__).resolve().parents[2]/'outputs'))
        self.path=Path(self.temp.name);self.now=[1000.]
        self.store=TaskStore(self.path/'tasks.db',lambda:self.now[0])
        self.task=self.store.create('owner','start','Original fixture','range-total')
    def tearDown(self):self.temp.cleanup()

    async def test_read_does_not_reserve_writer_lock(self):
        entered=threading.Event()
        def writer():
            with self.store.db() as db:
                entered.set();time.sleep(.25)
        thread=threading.Thread(target=writer);thread.start();entered.wait()
        started=time.monotonic()
        result=await asyncio.to_thread(self.store.get,'owner',self.task['id'])
        elapsed=time.monotonic()-started;thread.join()
        self.assertEqual(result['id'],self.task['id']);self.assertLess(elapsed,.15)

    async def test_async_write_wait_does_not_stall_event_loop(self):
        entered=threading.Event()
        def writer():
            with self.store.db():entered.set();time.sleep(.25)
        thread=threading.Thread(target=writer);thread.start();entered.wait()
        mutation=asyncio.create_task(self.store.io(self.store.stop,'owner',self.task['id'],'stop'))
        start=time.monotonic();await asyncio.sleep(.02)
        self.assertLess(time.monotonic()-start,.15)
        await mutation;thread.join()

    async def test_stop_survives_action_capacity(self):
        with self.store.db() as db:
            db.executemany('INSERT INTO demo_actions(owner,action_id,digest,task_id) VALUES(?,?,?,?)',
                [('owner','synthetic-'+str(i),'digest',self.task['id']) for i in range(1000)])
        self.assertEqual(self.store.stop('owner',self.task['id'],'emergency-stop')['state'],'stopped')

    async def test_expired_terminal_retention_frees_capacity_not_active_task(self):
        self.store.stop('owner',self.task['id'],'stop')
        active=self.store.create('owner','active','Active fixture','range-total')
        self.now[0]+=8*86400
        result=self.store.prune('owner')
        self.assertGreater(result['tasks'],0)
        self.assertEqual(self.store.get('owner',active['id'])['state'],'awaiting_approval')

    async def test_partial_pending_patch_reconciles_without_write_outside(self):
        store=LocalTaskStore(self.path/'local.db',self.path/'projects',lambda:self.now[0])
        task=store.create('owner','start','Original fixture','range-total');worker=LocalTaskWorker(store,'owner')
        project=worker.project(task['id']);(project/'.patch-pending').write_text(AFTER[:9])
        self.assertEqual(worker.project(task['id']),project)
        self.assertFalse((project/'.patch-pending').exists())

    async def test_orphan_process_record_requires_reconciliation(self):
        store=LocalTaskStore(self.path/'local.db',self.path/'projects',lambda:self.now[0])
        task=store.create('owner','start','Original fixture','range-total')
        store.approve('owner',task['id'],'approve',task['revision'],task['scope_digest'],task['nonce'])
        task=store.claim('owner',task['id'],'lost-worker')
        with store.db() as db:
            task=store._get(db,'owner',task['id']);task['process']={'pid':999999,'state':'owned','worker':'lost-worker','fence':task['fence']}
            db.execute('UPDATE demo_tasks SET body=? WHERE id=?',(json.dumps(task),task['id']))
        self.now[0]+=31;store.recover('owner',drained_tasks=[task['id']])
        self.assertEqual(store.get('owner',task['id'])['state'],'uncertain')

    async def test_owned_heartbeat_cannot_extend_expired_or_other_worker(self):
        t=self.task;self.store.approve('owner',t['id'],'approve',t['revision'],t['scope_digest'],t['nonce'])
        t=self.store.claim('owner',t['id'],'worker',lease_seconds=2);self.now[0]+=1
        self.assertTrue(self.store.heartbeat('owner',t['id'],'worker',t['fence'],t['generation'],lease_seconds=3))
        self.assertFalse(self.store.heartbeat('owner',t['id'],'other',t['fence'],t['generation']))
        self.now[0]+=4
        self.assertFalse(self.store.heartbeat('owner',t['id'],'worker',t['fence'],t['generation']))

    async def test_repeated_stop_receipts_are_bounded(self):
        for i in range(1100):self.store.stop('owner',self.task['id'],'stop-'+str(i))
        with self.store.db(write=False) as db:
            self.assertLessEqual(db.execute('SELECT COUNT(*) FROM demo_actions').fetchone()[0],1032)

    async def test_unresolved_failed_process_is_retained_and_reconciled(self):
        with self.store.db() as db:
            t=self.store._get(db,'owner',self.task['id'])
            t.update(state='failed',execution_kind='registered-local',process={'state':'owned'})
            db.execute('UPDATE demo_tasks SET body=? WHERE id=?',(json.dumps(t),t['id']))
        self.now[0]+=8*86400
        self.assertEqual(self.store.prune('owner')['tasks'],0)
        self.store.recover('owner')
        self.assertEqual(self.store.get('owner',t['id'])['state'],'uncertain')
