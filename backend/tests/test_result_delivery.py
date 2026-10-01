import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.agent_jobs import AgentJobStore, AgentJobWorker
from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.models import AgentReply, Surface


class ResultDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'test.db'
        ReminderStore(self.path)
        self.store = AgentJobStore(self.path)
        self.hub = DeviceHub()
        self.messages = []
        owner = self
        class Peer:
            async def send_json(self, message): owner.messages.append(message)
        self.peer = Peer()
        self.hub.register('a', self.peer, {'result_ack'})
        self.worker = AgentJobWorker(self.store, None, self.hub)

    def tearDown(self): self.directory.cleanup()

    def complete(self, request='request'):
        job = self.store.enqueue('a', 'hello', request_id=request)
        self.store.complete(job.id, AgentReply(text='Accepted reply', surface=Surface(kind='message', title='Friday'), tool='test'))
        return self.store.pending_results('a')[-1]

    def make_retryable(self, job):
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute('UPDATE agent_jobs SET result_attempted_at = ? WHERE id = ?', ((datetime.now(timezone.utc) - timedelta(seconds=11)).isoformat(), job.id))

    async def test_successful_socket_send_remains_pending_until_scoped_ack(self):
        job = self.complete()
        self.assertEqual(await self.worker.deliver_pending('a'), 1)
        result = next(m for m in self.messages if m['type'] == 'agent.result')
        self.assertEqual(result['delivery_id'], job.delivery_id)
        self.assertEqual(result['surface']['title'], 'Friday')
        self.assertEqual(len(self.store.pending_results('a')), 1)
        self.assertFalse(self.store.acknowledge_result('other', job.id, job.delivery_id))
        self.assertFalse(self.store.acknowledge_result('a', job.id, 'f' * 32))
        self.assertTrue(self.store.acknowledge_result('a', job.id, job.delivery_id))
        self.assertTrue(self.store.acknowledge_result('a', job.id, job.delivery_id))
        self.assertFalse(self.store.pending_results('a'))
        self.assertEqual(await self.worker.deliver_pending('a'), 0)

    async def test_restart_and_reconnect_replay_keep_id_and_do_not_repeat_tool_or_voice(self):
        job = self.complete()
        calls = []
        async def on_reply(*args): calls.append(args)
        self.worker.on_reply = on_reply
        await self.worker.deliver_pending('a')
        self.make_retryable(job)
        self.hub.disconnect('a', self.peer)
        self.hub.register('a', self.peer, {'result_ack'})
        self.messages.clear()
        restarted = AgentJobWorker(AgentJobStore(self.path), None, self.hub)
        restarted.on_reply = on_reply
        await restarted.deliver_pending('a')
        results = [m for m in self.messages if m['type'] == 'agent.result']
        self.assertEqual(results[0]['delivery_id'], job.delivery_id)
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(m['type'] in {'tool.activity', 'state'} for m in self.messages))
        self.assertTrue(restarted.store.acknowledge_result('a', job.id, job.delivery_id))
        self.assertFalse(AgentJobStore(self.path).pending_results('a'))

    async def test_later_legacy_connection_cannot_bypass_required_ack(self):
        job = self.complete()
        await self.worker.deliver_pending('a')
        self.hub.disconnect('a', self.peer)
        self.hub.register('a', self.peer)
        self.make_retryable(job)
        await self.worker.deliver_pending('a')
        self.assertEqual(len(self.store.pending_results('a')), 1)
        self.assertTrue(self.store.acknowledge_result('a', job.id, job.delivery_id))

    async def test_failed_terminal_result_is_acknowledged_too(self):
        job = self.store.enqueue('a', 'hello')
        job.attempts = 3
        self.store.fail(job, 'synthetic failure')
        await self.worker.deliver_pending('a')
        result = next(m for m in self.messages if m['type'] == 'agent.result')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(any(m['type'] == 'error' for m in self.messages))
        self.assertTrue(self.store.acknowledge_result('a', job.id, job.delivery_id))

    def test_unsent_or_running_result_cannot_be_acknowledged(self):
        job = self.store.enqueue('a', 'hello')
        self.assertFalse(self.store.acknowledge_result('a', job.id, job.delivery_id))
        self.store.complete(job.id, AgentReply(text='hi', surface=Surface(kind='message')))
        self.assertFalse(self.store.acknowledge_result('a', job.id, job.delivery_id))

    def test_retention_is_age_and_count_bounded_without_deleting_active_jobs(self):
        now = datetime.now(timezone.utc)
        old = self.complete('old')
        active = self.store.enqueue('a', 'active', request_id='active')
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute('UPDATE agent_jobs SET created_at=? WHERE id=?', ((now - timedelta(days=8)).isoformat(), old.id))
                connection.executemany("INSERT INTO agent_jobs(id,device_id,text,status,available_at,created_at,updated_at,result_delivery_id) VALUES(?,?,?,'failed',?,?,?,?)", [(f'bulk-{i}', 'b', 'hello', now.isoformat(), now.isoformat(), now.isoformat(), f'{i:032x}') for i in range(10002)])
        self.assertEqual(self.store.prune_results(now), 3)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_jobs WHERE status='failed'").fetchone()[0], 10000)
            self.assertEqual(connection.execute('SELECT status FROM agent_jobs WHERE id=?', (active.id,)).fetchone()[0], 'queued')
        self.assertEqual(len(self.store.pending_results('b')), 64)

    def test_existing_schema_migration_assigns_stable_delivery_ids(self):
        path = Path(self.directory.name) / 'old.db'
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute('CREATE TABLE agent_jobs(id TEXT PRIMARY KEY, device_id TEXT, text TEXT, status TEXT, attempts INTEGER, available_at TEXT, reply_json TEXT, error TEXT, result_delivered_at TEXT, created_at TEXT, updated_at TEXT)')
                now = datetime.now(timezone.utc).isoformat()
                connection.execute("INSERT INTO agent_jobs VALUES('old','a','hello','failed',3,?,NULL,'failed',NULL,?,?)", (now, now, now))
        migrated = AgentJobStore(path).pending_results('a')[0]
        self.assertEqual(len(migrated.delivery_id), 32)
        self.assertEqual(AgentJobStore(path).pending_results('a')[0].delivery_id, migrated.delivery_id)
