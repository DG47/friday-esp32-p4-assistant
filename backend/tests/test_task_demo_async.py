"""Synthetic async-storage and honest restart-state regressions."""
import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
import httpx
from app.task_demo import create_demo_app


def hold_writer(path, ready):
    db = sqlite3.connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        ready.set()
        time.sleep(.25)
        db.commit()
    finally:
        db.close()


class DemoAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.db'

    def tearDown(self): self.temp.cleanup()

    async def test_mutation_wait_does_not_block_event_loop(self):
        app = create_demo_app(self.path, enabled=True)
        ready = threading.Event()
        thread = threading.Thread(target=hold_writer, args=(self.path, ready))
        thread.start()
        await asyncio.to_thread(ready.wait)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1)), base_url='http://localhost') as client:
            request = asyncio.create_task(client.post('/api/tasks', headers={'X-Friday-Fixture': '1'}, json={'action_id': 'start', 'goal': 'Synthetic test', 'fixture': 'range-total'}))
            start = asyncio.get_running_loop().time()
            await asyncio.sleep(.03)
            self.assertLess(asyncio.get_running_loop().time() - start, .15)
            self.assertFalse(request.done())
            self.assertEqual((await request).status_code, 200)
        await asyncio.to_thread(thread.join)

    async def test_restart_does_not_infer_drain_from_missing_worker(self):
        now = [1000]
        app = create_demo_app(self.path, enabled=True, clock=lambda: now[0])
        store = app.state.task_store
        task = store.create('local-fixture-owner', 'start', 'Synthetic test', 'range-total')
        task = store.approve('local-fixture-owner', task['id'], 'approve', task['revision'], task['scope_digest'], task['nonce'])
        task = store.claim('local-fixture-owner', task['id'], 'previous-worker')
        store.stop('local-fixture-owner', task['id'], 'stop')
        now[0] = task['lease_until'] + 1
        async with app.router.lifespan_context(app):
            await asyncio.sleep(.05)
            current = await store.io(store.get, 'local-fixture-owner', task['id'])
            self.assertEqual(current['state'], 'stopping')

    async def test_idle_supervisor_waits_for_changes(self):
        from unittest.mock import patch
        app = create_demo_app(self.path, enabled=True)
        store = app.state.task_store
        with patch.object(store, 'list', wraps=store.list) as listing:
            async with app.router.lifespan_context(app):
                await asyncio.sleep(.18)
                self.assertLessEqual(listing.call_count, 2)
