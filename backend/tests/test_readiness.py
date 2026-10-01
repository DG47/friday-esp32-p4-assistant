import asyncio
import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent_jobs import AgentJobStore
from app.config import Settings
from app.db import ReminderStore
from app.device_hub import DeviceHub
from app.scenes import local_scene
from app.tools.calendar import MockCalendar
import test_device_socket


class ConfigurationTests(unittest.TestCase):
    def test_production_fails_closed(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, assistant_env='production')
        base = dict(_env_file=None, assistant_env='production', assistant_provider='ollama',
                    assistant_admin_token='a' * 64, assistant_simulator_token='b' * 64)
        self.assertEqual(Settings(**base).assistant_env, 'production')
        for changes in ({'assistant_simulator_token': 'a' * 64}, {'assistant_public_scheme': 'ws'},
                        {'assistant_provider': 'mock'}, {'assistant_provider': 'openai', 'openai_api_key': ''},
                        {'assistant_job_concurrency': 0}, {'stt_max_seconds': -1}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ValidationError):
                    Settings(**(base | changes))


class QueueBoundTests(unittest.TestCase):
    def test_queue_limit_preserves_retries_and_device_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            reminders = ReminderStore(Path(directory) / 'test.db')
            store = AgentJobStore(reminders.path)
            for number in range(32):
                store.enqueue('device-a', 'hello', request_id=f'a-{number}')
            with self.assertRaises(ValueError):
                store.enqueue('device-a', 'overflow')
            self.assertEqual(store.enqueue('device-a', 'hello', request_id='a-0').id, 'a-0')
            self.assertEqual(store.enqueue('device-b', 'hello', request_id='b-0').status, 'queued')


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_peer_does_not_block_healthy_peer(self):
        messages = []
        class Slow:
            async def send_json(self, message):
                await asyncio.sleep(60)
        class Good:
            async def send_json(self, message):
                messages.append(message)
        hub = DeviceHub(send_timeout=0.02)
        hub.register('a', Slow())
        hub.register('a', Good())
        self.assertEqual(await hub.broadcast('a', {'type': 'state'}), 1)
        self.assertEqual(len(hub.connections['a']), 1)
        self.assertEqual(messages, [{'type': 'state'}])

    async def test_concurrent_producers_serialize_each_peer(self):
        class Peer:
            active = False
            async def send_json(self, message):
                if self.active:
                    raise AssertionError('Concurrent socket writes')
                self.active = True
                await asyncio.sleep(0.005)
                self.active = False
        hub = DeviceHub()
        hub.register('a', Peer())
        self.assertEqual(await asyncio.gather(hub.broadcast('a', {}), hub.broadcast('a', {})), [1, 1])


class SceneSocketTests(unittest.TestCase):
    setUp = test_device_socket.DeviceSocketTests.setUp
    tearDown = test_device_socket.DeviceSocketTests.tearDown
    authenticate = test_device_socket.DeviceSocketTests.authenticate
    def test_malformed_hello_does_not_crash_connection(self):
        from app.main import app
        with TestClient(app) as client:
            with client.websocket_connect('/ws/device/friday-p3a') as socket:
                socket.receive_json()
                socket.send_json({'type': 'hello', 'capabilities': None})
                self.assertEqual(socket.receive_json()['code'], 'bad_message')
                socket.send_json({'type': 'hello', 'capabilities': 'display'})
                self.assertEqual(socket.receive_json()['code'], 'bad_message')

    def test_invalid_input_followed_by_working_local_scene(self):
        from app.main import app
        with TestClient(app) as client:
            with client.websocket_connect('/ws/device/friday-p3a') as socket:
                self.authenticate(socket, 'friday-p3a')
                for message in ({'type': 'utterance', 'text': {'oops': 'object'}},
                                {'type': 'utterance', 'text': 'x' * 4097},
                                {'type': 'reminder.ack', 'reminder_id': True}):
                    socket.send_json(message)
                    self.assertEqual(socket.receive_json()['type'], 'error')
                socket.send_json({'type': 'scene.action', 'action': 'status'})
                surface = socket.receive_json()['surface']
                self.assertEqual(surface['kind'], 'metric')
                self.assertIn('Not connected', str(surface['items']))
                socket.send_json({'type': 'scene.action', 'action': 'calendar'})
                # Calendar touch now announces progress before an honest unconnected scene.
                messages = [socket.receive_json() for _ in range(3)]
                self.assertEqual(messages[-1]['surface']['title'], 'Calendar not connected')
                socket.send_json({'type': 'scene.action', 'action': 'shell:rm'})
                self.assertEqual(socket.receive_json()['code'], 'bad_action')

    def test_reminder_scene_actions_are_scoped_and_queued(self):
        from app.main import app
        reminder = self.reminder_store.create('Read a book', datetime.now(timezone.utc) + timedelta(hours=1), 'friday-p3a')
        other = self.reminder_store.create('Private other device', datetime.now(timezone.utc) + timedelta(hours=1), 'other-device')
        with patch('app.main.handle_utterance') as handle:
            from unittest.mock import AsyncMock
            handle.side_effect = AsyncMock()
            with TestClient(app) as client:
                with client.websocket_connect('/ws/device/friday-p3a') as socket:
                    self.authenticate(socket, 'friday-p3a')
                    socket.send_json({'type': 'scene.action', 'action': 'reminders'})
                    scene = socket.receive_json()['surface']
                    self.assertEqual(len(scene['items']), 1)
                    socket.send_json({'type': 'scene.action', 'action': f'reminder:{other.id}'})
                    self.assertEqual(socket.receive_json()['code'], 'bad_action')
                    socket.send_json({'type': 'scene.action', 'action': f'complete:{reminder.id}', 'request_id': 'touch-1'})
                    # A subsequent local read acts as an ordering fence.
                    socket.send_json({'type': 'scene.action', 'action': 'status'})
                    socket.receive_json()
                    handle.assert_called_once_with('friday-p3a', f'complete reminder #{reminder.id}', request_id='touch-1')
                    self.assertFalse(self.reminder_store.active(device_id='friday-p3a')[0].completed)


class BackupTests(unittest.TestCase):
    def test_backup_restores_and_never_overwrites(self):
        script = Path(__file__).resolve().parents[2] / 'scripts' / 'backup_database.py'
        spec = importlib.util.spec_from_file_location('backup', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.db'
            destination = Path(directory) / 'backup.db'
            from contextlib import closing
            with closing(sqlite3.connect(source)) as database, database:
                database.execute('CREATE TABLE evidence (value TEXT)')
                database.execute("INSERT INTO evidence VALUES ('restorable')")
            module.backup_database(source, destination)
            with closing(sqlite3.connect(destination)) as database:
                self.assertEqual(database.execute('SELECT value FROM evidence').fetchone()[0], 'restorable')
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                module.backup_database(source, destination)

class LocalCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_timer_is_real_idempotent_and_survives_restart(self):
        from app.agent import FridayAgent
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            store = ReminderStore(path)
            settings = Settings(_env_file=None, assistant_provider='mock')
            agent = FridayAgent(settings, store)
            first = await agent.handle('device', 'set a timer for 2 minutes', request_id='timer-1')
            self.assertEqual(first.surface.kind, 'timer')
            self.assertLessEqual(first.surface.countdown_ms, 120000)
            reminder = store.active(device_id='device')[0]
            restarted = FridayAgent(settings, ReminderStore(path))
            repeated = await restarted.handle('device', 'set a timer for 2 minutes', request_id='timer-1')
            self.assertEqual(len(store.active(device_id='device')), 1)
            self.assertEqual(store.active(device_id='device')[0].due_at, reminder.due_at)
            self.assertLessEqual(repeated.surface.countdown_ms, first.surface.countdown_ms)
            scene = await restarted.handle('device', 'show my reminders', request_id='view-1')
            self.assertEqual(scene.surface.kind, 'list')
            self.assertIn('timer finished', scene.surface.items[0]['label'])

    async def test_touch_complete_requires_confirmation_after_restart(self):
        from app.agent import FridayAgent
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            store = ReminderStore(path)
            settings = Settings(_env_file=None, assistant_provider='mock')
            item = store.create('Do the task', datetime.now(timezone.utc) + timedelta(hours=1), 'device')
            agent = FridayAgent(settings, store)
            pending = await agent.handle('device', f'complete reminder #{item.id}', request_id='touch-complete')
            self.assertEqual(pending.surface.title, 'Confirmation required')
            self.assertFalse(store.active(device_id='device')[0].completed)
            restarted = FridayAgent(settings, ReminderStore(path))
            await restarted.handle('device', 'yes', request_id='confirm-complete')
            self.assertEqual(store.active(device_id='device'), [])
            await restarted.handle('device', 'yes', request_id='confirm-complete')
            self.assertEqual(store.active(device_id='device'), [])

    async def test_timer_out_of_range_creates_nothing(self):
        from app.agent import FridayAgent
        with tempfile.TemporaryDirectory() as directory:
            store = ReminderStore(Path(directory) / 'test.db')
            agent = FridayAgent(Settings(_env_file=None, assistant_provider='mock'), store)
            reply = await agent.handle('device', 'set a timer for 0 seconds')
            self.assertIn('one second', reply.text)
            self.assertEqual(store.active(device_id='device'), [])


class HealthTests(unittest.TestCase):
    def test_readiness_detects_dead_worker_and_diagnostics_is_private(self):
        from app.main import app
        with TestClient(app) as client:
            self.assertEqual(client.get('/ready').status_code, 200)
            self.assertEqual(client.get('/api/diagnostics').status_code, 403)
            class Done:
                def done(self): return True
            with patch.object(app.state, 'background_tasks', [Done()]):
                self.assertEqual(client.get('/ready').status_code, 503)
            response = client.get('/')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])


class WorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_slot_survives_transient_infrastructure_failure(self):
        from unittest.mock import AsyncMock
        from app.agent_jobs import AgentJobWorker
        worker = AgentJobWorker(None, None, DeviceHub())
        progressed = asyncio.Event()
        calls = 0
        async def once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError('temporary database lock')
            progressed.set()
            return False
        worker.process_once = once
        task = asyncio.create_task(worker._run_slot())
        try:
            await asyncio.wait_for(progressed.wait(), timeout=2)
            self.assertGreaterEqual(calls, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_revocation_closes_and_removes_connected_peers(self):
        class Peer:
            closed = None
            async def close(self, code): self.closed = code
        hub = DeviceHub()
        peer = Peer()
        hub.register('device', peer)
        await hub.close_device('device')
        self.assertEqual(peer.closed, 1008)
        self.assertEqual(hub.device_ids(), [])

class ConfigurationPrivacyTests(unittest.TestCase):
    def test_invalid_configuration_does_not_print_credential_values(self):
        sensitive = 'private-placeholder-do-not-log'
        with self.assertRaises(ValidationError) as caught:
            Settings(_env_file=None, assistant_env='production', assistant_admin_token=sensitive)
        self.assertNotIn(sensitive, str(caught.exception))


class BoundedLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_96_jobs_keep_per_device_order_and_bounded_parallelism(self):
        from app.agent_jobs import AgentJobWorker
        from app.models import AgentReply, Surface
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            ReminderStore(path)
            store = AgentJobStore(path)
            class Agent:
                active = set()
                peak = 0
                completed = {}
                async def handle(inner, device, text, request_id):
                    if device in inner.active:
                        raise AssertionError('Same-device overlap')
                    inner.active.add(device)
                    inner.peak = max(inner.peak, len(inner.active))
                    await asyncio.sleep(0.001)
                    inner.completed.setdefault(device, []).append(int(text))
                    inner.active.remove(device)
                    return AgentReply(text='done', surface=Surface(kind='message'))
            agent = Agent()
            worker = AgentJobWorker(store, agent, DeviceHub(), concurrency=4)
            for device in range(12):
                for turn in range(8):
                    store.enqueue(f'device-{device}', str(turn), f'{device}-{turn}')
            while True:
                results = await asyncio.gather(*(worker.process_once() for _ in range(4)))
                if not any(results): break
            self.assertLessEqual(agent.peak, 4)
            self.assertEqual(len(agent.completed), 12)
            self.assertTrue(all(turns == list(range(8)) for turns in agent.completed.values()))
            self.assertEqual(sum(len(store.pending_results(f'device-{d}')) for d in range(12)), 96)

class TimerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_reconnection_does_not_restart_countdown(self):
        from app.agent_jobs import AgentJob, AgentJobWorker
        from app.models import AgentReply, Surface
        class Peer:
            messages = []
            async def send_json(inner, message): inner.messages.append(message)
        hub = DeviceHub()
        peer = Peer()
        hub.register('device', peer)
        worker = AgentJobWorker(None, None, hub)
        reply = AgentReply(text='Timer was set', surface=Surface(kind='timer', countdown_ms=120000,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)))
        job = AgentJob(id='timer', device_id='device', text='timer', status='completed', attempts=1)
        await worker._publish(job, reply)
        delivered = next(message['surface'] for message in peer.messages if message['type'] == 'surface')
        self.assertLessEqual(delivered['countdown_ms'], 30000)

class SocketAdmissionTests(unittest.TestCase):
    setUp = test_device_socket.DeviceSocketTests.setUp
    tearDown = test_device_socket.DeviceSocketTests.tearDown
    authenticate = test_device_socket.DeviceSocketTests.authenticate

    def test_fifth_peer_is_rejected_and_closed_sockets_are_removed(self):
        from contextlib import ExitStack
        from fastapi import WebSocketDisconnect
        from app.main import app, active_sockets
        with TestClient(app) as client:
            with ExitStack() as stack:
                for number in range(4):
                    socket = stack.enter_context(client.websocket_connect('/ws/device/admission'))
                    self.authenticate(socket, 'admission')
                with client.websocket_connect('/ws/device/admission') as extra:
                    self.assertEqual(extra.receive_json()['type'], 'hello.required')
                    extra.send_json({'type': 'hello', 'device_token': 'a' * 64})
                    with self.assertRaises(WebSocketDisconnect) as caught:
                        extra.receive_json()
                    self.assertEqual(caught.exception.code, 1013)
        self.assertEqual(active_sockets, set())

class ScenePaginationTests(unittest.TestCase):
    def test_reminder_pages_cover_all_items_without_cross_device_leaks(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReminderStore(Path(directory) / 'test.db')
            settings = Settings(_env_file=None, assistant_provider='mock')
            for number in range(5):
                store.create(f'Task {number}', datetime.now(timezone.utc) + timedelta(hours=1), 'device')
            store.create('Other device private', datetime.now(timezone.utc) + timedelta(hours=1), 'other')
            first = local_scene('reminders', 'device', settings, store, MockCalendar(settings.assistant_timezone))
            second = local_scene('reminders:1', 'device', settings, store, MockCalendar(settings.assistant_timezone))
            self.assertEqual(len(first.items), 4)
            self.assertEqual(len(second.items), 1)
            self.assertIn('reminders:1', [action.id for action in first.actions])
            self.assertIn('reminders:0', [action.id for action in second.actions])
            self.assertNotIn('Other device private', str(first.items) + str(second.items))
