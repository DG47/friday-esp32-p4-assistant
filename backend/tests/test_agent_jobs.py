import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent_jobs import AgentJobStore, AgentJobWorker
from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.models import AgentReply, Surface


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def accept(self):
        pass

    async def send_json(self, message):
        self.messages.append(message)


class FakeAgent:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    async def handle(self, device_id, text, request_id=None):
        self.calls.append((device_id, text, request_id))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary model failure")
        return AgentReply(
            text="Completed.",
            surface=Surface(kind="message", title="Friday", subtitle="Completed."),
            provider="test",
        )


class BlockingAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Queue()
        self.release = asyncio.Event()

    async def handle(self, device_id, text, request_id=None):
        self.calls.append((device_id, text, request_id))
        await self.started.put(device_id)
        await self.release.wait()
        return AgentReply(
            text="Completed.",
            surface=Surface(kind="message", title="Friday", subtitle="Completed."),
            provider="test",
        )


class AgentJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "jobs.db"
        ReminderStore(self.path)
        self.store = AgentJobStore(self.path)
        self.hub = DeviceHub()

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_worker_persists_and_delivers_agent_result(self):
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        job = self.store.enqueue("device-1", "Do the thing", request_id="request-1")
        worker = AgentJobWorker(self.store, FakeAgent(), self.hub)

        self.assertTrue(await worker.process_once())

        saved = self.store.get(job.id)
        self.assertEqual(saved.status, "completed")
        self.assertEqual(saved.attempts, 1)
        self.assertEqual(
            [message["type"] for message in socket.messages],
            ["surface", "assistant.text", "state"],
        )
        self.assertEqual(socket.messages[1]["job_id"], "request-1")
        self.assertEqual(self.store.pending_results("device-1"), [])

    async def test_interrupted_job_is_recovered_after_restart(self):
        job = self.store.enqueue("device-1", "Recover this", request_id="request-2")
        claimed = self.store.claim_next()
        self.assertEqual(claimed.id, job.id)
        self.assertEqual(self.store.get(job.id).status, "running")

        restarted = AgentJobStore(self.path)
        self.assertEqual(restarted.recover_interrupted(), 0)
        after_lease = datetime.now(timezone.utc) + timedelta(minutes=4)
        self.assertEqual(restarted.recover_interrupted(after_lease), 1)
        self.assertEqual(restarted.get(job.id).status, "queued")

    async def test_only_one_worker_can_claim_a_job(self):
        self.store.enqueue("device-1", "Claim once", request_id="request-claim")
        second_store = AgentJobStore(self.path)

        first = self.store.claim_next()
        second = second_store.claim_next()

        self.assertEqual(first.id, "request-claim")
        self.assertIsNone(second)

    async def test_different_devices_can_run_in_parallel(self):
        self.store.enqueue("device-1", "First", request_id="parallel-1")
        self.store.enqueue("device-2", "Second", request_id="parallel-2")
        agent = BlockingAgent()
        worker = AgentJobWorker(self.store, agent, self.hub, concurrency=2)

        first_task = asyncio.create_task(worker.process_once())
        first_device = await asyncio.wait_for(agent.started.get(), timeout=1)
        second_task = asyncio.create_task(worker.process_once())
        second_device = await asyncio.wait_for(agent.started.get(), timeout=1)

        self.assertEqual({first_device, second_device}, {"device-1", "device-2"})
        agent.release.set()
        self.assertTrue(await first_task)
        self.assertTrue(await second_task)

    async def test_worker_pool_uses_configured_concurrency(self):
        self.store.enqueue("device-1", "First", request_id="pool-1")
        self.store.enqueue("device-2", "Second", request_id="pool-2")
        agent = BlockingAgent()
        worker = AgentJobWorker(self.store, agent, self.hub, concurrency=2)
        run_task = asyncio.create_task(worker.run())
        try:
            started = {
                await asyncio.wait_for(agent.started.get(), timeout=1),
                await asyncio.wait_for(agent.started.get(), timeout=1),
            }
            self.assertEqual(started, {"device-1", "device-2"})
        finally:
            agent.release.set()
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task

    async def test_same_device_jobs_never_run_in_parallel(self):
        self.store.enqueue("device-1", "First", request_id="serial-1")
        self.store.enqueue("device-1", "Second", request_id="serial-2")
        agent = BlockingAgent()
        worker = AgentJobWorker(self.store, agent, self.hub, concurrency=2)

        first_task = asyncio.create_task(worker.process_once())
        await asyncio.wait_for(agent.started.get(), timeout=1)
        self.assertFalse(await worker.process_once())
        self.assertEqual(len(agent.calls), 1)

        agent.release.set()
        self.assertTrue(await first_task)
        self.assertTrue(await worker.process_once())
        self.assertEqual(len(agent.calls), 2)

    async def test_stale_queued_job_is_not_executed(self):
        job = self.store.enqueue("device-1", "Old request", request_id="stale-queued")
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE agent_jobs SET created_at = ? WHERE id = ?", (old, job.id)
            )
            connection.commit()
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        agent = FakeAgent()
        worker = AgentJobWorker(
            self.store, agent, self.hub, max_age_seconds=60
        )

        self.assertFalse(await worker.process_once())
        self.assertEqual(await worker.deliver_pending("device-1"), 1)
        self.assertEqual(agent.calls, [])
        self.assertEqual(socket.messages[0]["code"], "agent_stale")

    async def test_late_completed_result_is_not_presented_as_current(self):
        job = self.store.enqueue("device-1", "Slow request", request_id="late-result")
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE agent_jobs SET created_at = ? WHERE id = ?", (old, job.id)
            )
            connection.commit()
        claimed = self.store.claim_next(max_age_seconds=3600)
        status = self.store.complete(
            claimed.id,
            AgentReply(
                text="Stale answer",
                surface=Surface(kind="message", title="Friday", subtitle="Stale answer"),
            ),
            max_age_seconds=60,
        )
        socket = FakeWebSocket()
        await self.hub.connect("device-1", socket)
        worker = AgentJobWorker(self.store, FakeAgent(), self.hub, max_age_seconds=60)

        self.assertEqual(status, "late")
        self.assertEqual(await worker.deliver_pending("device-1"), 1)
        self.assertEqual(socket.messages[0]["code"], "agent_stale")
        self.assertNotIn("Stale answer", str(socket.messages))

    async def test_completed_result_waits_for_reconnection(self):
        job = self.store.enqueue("offline", "Finish offline", request_id="request-offline")
        worker = AgentJobWorker(self.store, FakeAgent(), self.hub)

        self.assertTrue(await worker.process_once())
        self.assertEqual(len(self.store.pending_results("offline")), 1)

        socket = FakeWebSocket()
        await self.hub.connect("offline", socket)
        self.assertEqual(await worker.deliver_pending("offline"), 1)
        self.assertEqual(await worker.deliver_pending("offline"), 0)
        self.assertEqual(self.store.pending_results("offline"), [])
        self.assertEqual(socket.messages[1]["job_id"], job.id)

    async def test_failed_job_is_retried_without_losing_request_identity(self):
        agent = FakeAgent(failures=1)
        worker = AgentJobWorker(self.store, agent, self.hub)
        job = self.store.enqueue("device-1", "Retry this", request_id="request-3")

        self.assertTrue(await worker.process_once())
        self.assertEqual(self.store.get(job.id).status, "queued")
        self.assertEqual(agent.calls[0][2], "request-3")

    async def test_retry_preserves_same_device_turn_order(self):
        agent = FakeAgent(failures=1)
        worker = AgentJobWorker(self.store, agent, self.hub)
        self.store.enqueue("device-1", "First", request_id="ordered-1")
        self.store.enqueue("device-1", "Second", request_id="ordered-2")

        self.assertTrue(await worker.process_once())
        self.assertFalse(await worker.process_once())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "UPDATE agent_jobs SET available_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "ordered-1"),
            )
            connection.commit()
        self.assertTrue(await worker.process_once())
        self.assertTrue(await worker.process_once())

        self.assertEqual(
            [call[1] for call in agent.calls], ["First", "First", "Second"]
        )

    async def test_terminal_failure_waits_for_reconnection(self):
        job = self.store.enqueue("offline", "Fail offline", request_id="request-failed")
        claimed = self.store.claim_next()
        self.store.fail(claimed, "permanent failure", max_attempts=1)
        worker = AgentJobWorker(self.store, FakeAgent(), self.hub)

        self.assertEqual(len(self.store.pending_results("offline")), 1)
        socket = FakeWebSocket()
        await self.hub.connect("offline", socket)

        self.assertEqual(await worker.deliver_pending("offline"), 1)
        self.assertEqual(socket.messages[0]["type"], "error")
        self.assertEqual(socket.messages[0]["job_id"], job.id)
        self.assertEqual(self.store.pending_results("offline"), [])


if __name__ == "__main__":
    unittest.main()
