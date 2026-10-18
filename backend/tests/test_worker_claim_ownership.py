import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from app.agent_jobs import AgentJobStore, AgentJobWorker, UNCERTAIN_CLAIM_ERROR, UncertainExecutionPending
from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.models import AgentReply, Surface
from test_agent_jobs import BlockingAgent, FakeAgent, FakeWebSocket


class ClaimOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'jobs.db'
        ReminderStore(self.path)
        self.store = AgentJobStore(self.path)
        self.hub = DeviceHub()

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_live_expired_claim_is_not_requeued_but_other_interrupted_claim_recovers(self):
        job = self.store.enqueue('device', 'synthetic', request_id='active')
        agent = BlockingAgent()
        worker = AgentJobWorker(self.store, agent, self.hub)
        task = asyncio.create_task(worker.process_once())
        try:
            await asyncio.wait_for(agent.started.get(), 1)
            other = self.store.enqueue('other', 'synthetic', request_id='interrupted')
            self.assertEqual(self.store.claim_next().id, other.id)
            future = datetime.now(timezone.utc) + timedelta(minutes=4)
            self.assertEqual(worker.recover_interrupted(future), 1)
            self.assertEqual(self.store.get(job.id).status, 'running')
            claimed = self.store.claim_next(future)
            self.assertEqual(claimed.id, other.id)
            self.assertIsNone(self.store.claim_next(future))
        finally:
            agent.release.set()
            await asyncio.wait_for(task, 1)
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(self.store.get(job.id).attempts, 1)
        self.assertEqual(self.store.get(job.id).status, 'completed')

    async def test_timeout_resistant_handler_remains_owned_until_real_return(self):
        cancelled = asyncio.Event()
        class Resistant(BlockingAgent):
            async def handle(self, *args, **kwargs):
                try:
                    return await super().handle(*args, **kwargs)
                except asyncio.CancelledError:
                    cancelled.set()
                    await self.release.wait()
                    return AgentReply(text='Synthetic', surface=Surface(kind='message', title='Synthetic'), provider='test')
        agent = Resistant()
        job = self.store.enqueue('device', 'synthetic')
        worker = AgentJobWorker(self.store, agent, self.hub, timeout_seconds=.01)
        task = asyncio.create_task(worker.process_once())
        try:
            await asyncio.wait_for(cancelled.wait(), 1)
            self.assertEqual(worker.recover_interrupted(datetime.now(timezone.utc)+timedelta(minutes=4)), 0)
            self.assertIsNone(self.store.claim_next(datetime.now(timezone.utc)+timedelta(minutes=4)))
        finally:
            agent.release.set()
            await asyncio.wait_for(task, 1)
        self.assertEqual(self.store.get(job.id).status, 'completed')
        self.assertEqual(len(agent.calls), 1)

    async def test_cancelled_execution_is_not_automatically_replayed_after_restart(self):
        agent = BlockingAgent()
        job = self.store.enqueue('device', 'synthetic')
        worker = AgentJobWorker(self.store, agent, self.hub)
        task = asyncio.create_task(worker.process_once())
        await asyncio.wait_for(agent.started.get(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertEqual(self.store.get(job.id).status, 'running')
        self.assertEqual(self.store.get(job.id).error, UNCERTAIN_CLAIM_ERROR)
        reopened = AgentJobStore(self.path)
        self.assertEqual(reopened.recover_interrupted(datetime.now(timezone.utc)+timedelta(days=1)), 0)
        self.assertIsNone(reopened.claim_next(datetime.now(timezone.utc)+timedelta(days=1)))
        self.assertEqual(len(agent.calls), 1)

    async def test_cancelled_parent_cannot_replay_shielded_work_even_after_late_finish(self):
        started, release = asyncio.Event(), asyncio.Event()
        effects = []
        native = None
        async def external_fixture():
            started.set()
            await release.wait()
            effects.append('one synthetic effect')
        class Shielded:
            async def handle(self, *args, **kwargs):
                nonlocal native
                native = asyncio.create_task(external_fixture())
                await asyncio.shield(native)
                return AgentReply(text='Synthetic', surface=Surface(kind='message', title='Synthetic'), provider='test')
        job = self.store.enqueue('device', 'synthetic')
        worker = AgentJobWorker(self.store, Shielded(), self.hub)
        parent = asyncio.create_task(worker.process_once())
        try:
            await asyncio.wait_for(started.wait(), 1)
            parent.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(parent, 1)
            self.assertFalse(native.done())
            future = datetime.now(timezone.utc)+timedelta(days=1)
            self.assertEqual(worker.recover_interrupted(future), 0)
            self.assertEqual(AgentJobStore(self.path).recover_interrupted(future), 0)
            release.set()
            await asyncio.wait_for(native, 1)
            self.assertEqual(AgentJobStore(self.path).recover_interrupted(future), 0)
            self.assertEqual(effects, ['one synthetic effect'])
            self.assertEqual(self.store.get(job.id).error, UNCERTAIN_CLAIM_ERROR)
        finally:
            release.set()
            if native is not None:
                await asyncio.wait_for(native, 1)

    async def test_timeout_with_live_shielded_effect_fails_closed_and_notifies_peer(self):
        release = asyncio.Event()
        effects = []
        native = None
        async def effect():
            await release.wait()
            effects.append('one synthetic effect')
        class Shielded:
            async def handle(self, *args, **kwargs):
                nonlocal native
                native = asyncio.create_task(effect())
                await asyncio.shield(native)
        socket = FakeWebSocket()
        await self.hub.connect('device', socket)
        job = self.store.enqueue('device', 'synthetic')
        worker = AgentJobWorker(self.store, Shielded(), self.hub, timeout_seconds=.01)
        try:
            self.assertTrue(await worker.process_once())
            self.assertFalse(native.done())
            saved = self.store.get(job.id)
            self.assertEqual((saved.status, saved.attempts, saved.error), ('running', 1, UNCERTAIN_CLAIM_ERROR))
            self.assertEqual(AgentJobStore(self.path).recover_interrupted(datetime.now(timezone.utc)+timedelta(days=1)), 0)
            self.assertTrue(any(m.get('code')=='execution_outcome_uncertain' for m in socket.messages))
            self.assertFalse(any(m.get('type')=='agent.result' and m.get('status')=='completed' for m in socket.messages))
            with self.assertRaises(UncertainExecutionPending):
                self.store.enqueue('device', 'another synthetic request')
            self.assertFalse(await worker.process_once())
        finally:
            release.set()
            if native is not None:
                await asyncio.wait_for(native, 1)
        self.assertEqual(effects, ['one synthetic effect'])
