from contextlib import closing
import hashlib
import asyncio
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient
from app.worker_contract import WorkerContractStore, create_worker_contract_app

# Public original synthetic fixture, never usable as a deployed credential.
SYNTHETIC = 'c' * 64
HEADERS = {'Authorization': 'Bearer ' + SYNTHETIC}
BINDINGS = {'synthetic-worker': ('synthetic-owner', hashlib.sha256(SYNTHETIC.encode()).hexdigest())}


class WorkerContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'worker.db'
        self.now = [1000.0]
        self.store = WorkerContractStore(self.path, lambda: self.now[0])

    def tearDown(self):
        self.store.release()
        self.temp.cleanup()

    def app(self):
        return create_worker_contract_app(self.store, credentials=BINDINGS, enabled=True)

    def hello(self, client):
        response = client.post('/internal/workers/hello', json={'worker_id': 'synthetic-worker'}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def session(self, hello):
        return {'worker_id': 'synthetic-worker', 'generation': hello['generation'], 'coordinator_epoch': hello['coordinator_epoch']}

    def test_plain_http_rejects_even_a_configured_credential(self):
        with TestClient(self.app()) as client:
            response = client.post('/internal/workers/hello', json={'worker_id': 'synthetic-worker'}, headers=HEADERS)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['detail'], 'Worker transport requires TLS')

    def test_disabled_default_admits_nothing(self):
        with TestClient(create_worker_contract_app(self.store)) as client:
            self.assertEqual(client.post('/internal/workers/hello', json={'worker_id': 'synthetic-worker'}, headers=HEADERS).status_code, 503)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM worker_connections').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            create_worker_contract_app(self.store, enabled=True)

    def test_authentication_and_owner_cannot_be_supplied_by_worker(self):
        with TestClient(self.app(), base_url='https://testserver') as client:
            for headers in ({}, {'Authorization': 'Bearer ' + 'd' * 64}):
                self.assertEqual(client.post('/internal/workers/hello', json={'worker_id': 'synthetic-worker'}, headers=headers).status_code, 401)
            self.assertEqual(client.post('/internal/workers/hello', json={'worker_id': 'synthetic-worker', 'owner': 'other'}, headers=HEADERS).status_code, 422)
            self.assertEqual(client.post('/internal/workers/hello', json={'worker_id': 'other'}, headers=HEADERS).status_code, 401)

    def test_expired_heartbeat_is_offline_and_cannot_reanimate_session(self):
        with TestClient(self.app(), base_url='https://testserver') as client:
            hello = self.hello(client)
            self.now[0] += 31
            self.assertEqual(client.post('/internal/workers/heartbeat', json=self.session(hello), headers=HEADERS).status_code, 409)
            status = client.get('/internal/workers/synthetic-worker/status', headers=HEADERS).json()
            self.assertEqual(status['reason'], 'mac_worker_offline')
            self.assertEqual(status['termination'], 'unproven')
            self.assertEqual(status['execution'], 'not_implemented')

    def test_authenticated_reconnect_fences_previous_session(self):
        with TestClient(self.app(), base_url='https://testserver') as client:
            old = self.hello(client)
            fresh = self.hello(client)
            self.assertGreater(fresh['generation'], old['generation'])
            self.assertEqual(client.post('/internal/workers/heartbeat', json=self.session(old), headers=HEADERS).status_code, 409)
            self.assertEqual(client.post('/internal/workers/heartbeat', json=self.session(fresh), headers=HEADERS).status_code, 200)
            self.assertEqual(client.post('/internal/workers/disconnect', json=self.session(old), headers=HEADERS).status_code, 409)

    def test_shutdown_and_restart_persist_fencing_without_termination_claim(self):
        with TestClient(self.app(), base_url='https://testserver') as client:
            old = self.hello(client)
        reopened = WorkerContractStore(self.path, lambda: self.now[0])
        with TestClient(create_worker_contract_app(reopened, credentials=BINDINGS, enabled=True), base_url='https://testserver') as client:
            self.assertEqual(client.get('/internal/workers/synthetic-worker/status', headers=HEADERS).json()['state'], 'offline')
            self.assertEqual(client.post('/internal/workers/heartbeat', json=self.session(old), headers=HEADERS).status_code, 409)
            fresh = self.hello(client)
            self.assertGreater(fresh['generation'], old['generation'])

    def test_second_coordinator_fails_closed(self):
        with TestClient(self.app(), base_url='https://testserver') as client:
            self.hello(client)
            other = WorkerContractStore(self.path, lambda: self.now[0])
            with self.assertRaises(BlockingIOError):
                with TestClient(create_worker_contract_app(other, credentials=BINDINGS, enabled=True), base_url='https://testserver'):
                    pass
            self.assertEqual(client.get('/internal/workers/synthetic-worker/status', headers=HEADERS).json()['state'], 'connected')

    def test_disconnect_is_offline_not_stopped_and_has_no_command_route(self):
        with TestClient(self.app(), base_url='https://testserver') as client:
            response = client.post('/internal/workers/disconnect', json=self.session(self.hello(client)), headers=HEADERS)
            self.assertEqual(response.json()['termination'], 'unproven')
            self.assertEqual(client.post('/internal/workers/execute', json={'command': 'synthetic'}, headers=HEADERS).status_code, 404)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


class WorkerStartupCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_losing_startup_preserves_winning_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkerContractStore(Path(directory) / 'original-synthetic-overlap.db')
            app = create_worker_contract_app(store, credentials=BINDINGS, enabled=True)
            acquire = store.acquire
            guard = threading.Lock()
            sequence = [0]
            entered = [threading.Event(), threading.Event()]
            finish = [threading.Event(), threading.Event()]

            def delayed(*args):
                with guard:
                    index = sequence[0]
                    sequence[0] += 1
                entered[index].set()
                finish[index].wait(3)
                return acquire(*args)

            winner = app.router.lifespan_context(app)
            loser = app.router.lifespan_context(app)
            tasks = []
            winner_started = False
            try:
                with patch.object(store, 'acquire', side_effect=delayed):
                    tasks.append(asyncio.create_task(winner.__aenter__()))
                    self.assertTrue(await asyncio.to_thread(entered[0].wait, 2))
                    tasks.append(asyncio.create_task(loser.__aenter__()))
                    self.assertTrue(await asyncio.to_thread(entered[1].wait, 2))
                    tasks[1].cancel()
                    finish[0].set()
                    await tasks[0]
                    winner_started = True
                    winning_epoch = store.epoch
                    finish[1].set()
                    with self.assertRaises(asyncio.CancelledError):
                        await tasks[1]
                    self.assertIsNotNone(store.lock_fd)
                    self.assertEqual(store.epoch, winning_epoch)
                    self.assertEqual(store.status('synthetic-owner', 'synthetic-worker')['execution'], 'not_implemented')
            finally:
                for gate in finish:
                    gate.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                if winner_started:
                    await winner.__aexit__(None, None, None)
                store.release()

    async def test_cancelled_startup_drains_admitted_acquire_then_releases_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkerContractStore(Path(directory) / 'synthetic.db')
            app = create_worker_contract_app(store, credentials=BINDINGS, enabled=True)
            entered, finish = threading.Event(), threading.Event()
            acquire = store.acquire
            def delayed(*args):
                entered.set()
                finish.wait(2)
                acquire(*args)
            context = app.router.lifespan_context(app)
            with patch.object(store, 'acquire', side_effect=delayed):
                task = asyncio.create_task(context.__aenter__())
                await asyncio.to_thread(entered.wait, 2)
                task.cancel()
                finish.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNone(store.lock_fd)
            other = WorkerContractStore(store.path)
            other.acquire()
            other.release()
