"""Backups with retention, metrics exposition, alert sink, worker watchdog and the health probe."""
import asyncio
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import metrics
from app.alerts import AlertSink, watch_tasks
from app.backup import BackupScheduler, backup_database, backup_time
from app.config import Settings
from app.db import ReminderStore

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace('.py', ''), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackupSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / 'friday.db'
        ReminderStore(self.database).create('private text', datetime.now(timezone.utc) + timedelta(hours=1), 'device')
        self.now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def scheduler(self, **overrides):
        options = dict(interval_seconds=3600, keep_count=2, keep_days=30)
        options.update(overrides)
        return BackupScheduler(self.database, self.root / 'backups', **options)

    def test_backup_is_created_when_due_and_skipped_when_fresh(self):
        scheduler = self.scheduler()
        self.assertTrue(scheduler.due(self.now))
        result = scheduler.run_once(self.now)
        created = Path(result['created'])
        self.assertEqual(created.name, 'friday-20261030T120000Z.db')
        self.assertEqual(created.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'backups').stat().st_mode & 0o777, 0o700)
        self.assertEqual(backup_time(created), self.now)
        with closing(sqlite3.connect(created)) as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM reminders').fetchone()[0], 1)
        self.assertEqual(scheduler.status.consecutive_failures, 0)
        self.assertEqual(scheduler.status.retained, 1)
        self.assertFalse(scheduler.due(self.now + timedelta(minutes=59)))
        self.assertIsNone(scheduler.run_once(self.now + timedelta(minutes=59))['created'])
        self.assertTrue(scheduler.due(self.now + timedelta(hours=1)))

    def test_retention_keeps_count_and_age_but_never_the_newest(self):
        scheduler = self.scheduler(keep_count=2, keep_days=1)
        for hours in (0, 2, 4):
            scheduler.run_once(self.now + timedelta(hours=hours))
        names = [path.name for path in scheduler.backups()]
        self.assertEqual(names, ['friday-20261030T160000Z.db', 'friday-20261030T140000Z.db'])
        later = self.now + timedelta(days=3)
        scheduler.run_once(later)
        self.assertEqual([path.name for path in scheduler.backups()], ['friday-20261102T120000Z.db'])
        self.assertEqual(scheduler.status.retained, 1)

    def test_unrelated_files_in_the_directory_are_ignored(self):
        scheduler = self.scheduler()
        (self.root / 'backups').mkdir()
        (self.root / 'backups' / 'notes.txt').write_text('keep me')
        (self.root / 'backups' / 'friday-bad-name.db').write_text('not a backup')
        scheduler.run_once(self.now)
        scheduler.run_once(self.now + timedelta(hours=1))
        scheduler.run_once(self.now + timedelta(hours=2))
        self.assertTrue((self.root / 'backups' / 'notes.txt').exists())
        self.assertTrue((self.root / 'backups' / 'friday-bad-name.db').exists())
        self.assertEqual(len(scheduler.backups()), 2)

    def test_failure_is_recorded_without_leaking_paths_and_alerts_once(self):
        scheduler = BackupScheduler(self.root / 'missing.db', self.root / 'backups', 3600, on_failure=None)
        result = scheduler.run_once(self.now)
        self.assertIsNone(result['created'])
        self.assertEqual(scheduler.status.last_error, 'ValueError')
        self.assertEqual(scheduler.status.consecutive_failures, 1)
        self.assertNotIn(str(self.root), str(scheduler.status.as_dict()))
        alerts = []

        async def record(kind, message):
            alerts.append((kind, message))

        scheduler.on_failure = record
        asyncio.run(scheduler.tick(self.now + timedelta(hours=1)))
        self.assertEqual(scheduler.status.consecutive_failures, 2)
        self.assertEqual(alerts[0][0], 'backup_failed')
        self.assertIn('ValueError', alerts[0][1])
        self.assertNotIn(str(self.root), alerts[0][1])

    def test_disabled_scheduler_never_runs(self):
        scheduler = self.scheduler(interval_seconds=0)
        self.assertFalse(scheduler.enabled)
        self.assertFalse(scheduler.due(self.now))
        self.assertIsNone(scheduler.run_once(self.now)['created'])
        self.assertFalse((self.root / 'backups').exists())

    def test_backup_never_overwrites_an_existing_file(self):
        destination = self.root / 'copy.db'
        backup_database(self.database, destination)
        with self.assertRaises(FileExistsError):
            backup_database(self.database, destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_script_wrapper_still_exposes_backup_database(self):
        module = load_script('backup_database.py')
        module.backup_database(self.database, self.root / 'wrapped.db')
        self.assertTrue((self.root / 'wrapped.db').is_file())


class MetricsTests(unittest.TestCase):
    def setUp(self):
        metrics.reset()

    def tearDown(self):
        metrics.reset()

    def test_render_includes_types_labels_and_counters(self):
        metrics.increment('friday_alerts_total', kind='backup_failed')
        metrics.increment('friday_alerts_total', kind='backup_failed')
        metrics.increment('friday_reminders_delivered_total')
        text = metrics.render([('friday_up', {}, 1), ('friday_agent_jobs', {'status': 'que"ued'}, 2.0)],
                              {'friday_up': 'process answers'})
        self.assertIn('# HELP friday_up process answers\n# TYPE friday_up gauge\nfriday_up 1\n', text)
        self.assertIn('friday_agent_jobs{status="que\\"ued"} 2\n', text)
        self.assertIn('# TYPE friday_alerts_total counter\nfriday_alerts_total{kind="backup_failed"} 2\n', text)
        self.assertIn('friday_reminders_delivered_total 1\n', text)
        self.assertTrue(text.endswith('\n'))


class AlertSinkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        metrics.reset()
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / 'alerts.log'
        helper = Path(self.temp.name) / 'record.py'
        helper.write_text('import sys\nopen(sys.argv[1], "a").write(" ".join(sys.argv[2:]) + "\\n")\n')
        self.command = f'{sys.executable} {helper} {self.log}'

    def tearDown(self):
        metrics.reset()
        self.temp.cleanup()

    async def test_command_runs_once_per_kind_within_the_cooldown(self):
        clock = [1000.0]
        sink = AlertSink(self.command, cooldown_seconds=600, clock=lambda: clock[0])
        self.assertTrue(await sink.notify('backup_failed', 'first'))
        self.assertFalse(await sink.notify('backup_failed', 'second'))
        self.assertTrue(await sink.notify('worker_stopped', 'other kind'))
        clock[0] += 601
        self.assertTrue(await sink.notify('backup_failed', 'third'))
        self.assertEqual(self.log.read_text().splitlines(),
                         ['backup_failed first', 'worker_stopped other kind', 'backup_failed third'])
        self.assertEqual(dict((labels['kind'], value) for _, labels, value in metrics.counters()),
                         {'backup_failed': 3, 'worker_stopped': 1})

    async def test_missing_or_broken_command_never_raises(self):
        self.assertFalse(await AlertSink('').notify('backup_failed', 'logged only'))
        self.assertFalse(await AlertSink('/nonexistent/alert-hook').notify('backup_failed', 'broken'))
        self.assertEqual(metrics.counters()[0][2], 2)

    async def test_watchdog_reports_a_stopped_task_once(self):
        alerts = []

        class Sink:
            async def notify(self, kind, message):
                alerts.append((kind, message))

        async def finished():
            return None

        async def forever():
            await asyncio.sleep(3600)

        done = asyncio.create_task(finished(), name='friday-agent-job-worker')
        alive = asyncio.create_task(forever(), name='friday-reminder-scheduler')
        await done
        watchdog = asyncio.create_task(watch_tasks([done, alive], Sink(), interval_seconds=0.01))
        try:
            await asyncio.sleep(0.1)
        finally:
            watchdog.cancel()
            alive.cancel()
            await asyncio.gather(watchdog, alive, return_exceptions=True)
        self.assertEqual(alerts, [('worker_stopped', 'Background task friday-agent-job-worker stopped')])


class MetricsEndpointTests(unittest.TestCase):
    def test_metrics_require_loopback_and_token_then_expose_counts_only(self):
        from app.main import app
        with TestClient(app) as client:
            self.assertEqual(client.get('/metrics').status_code, 403)
            with patch('app.main.is_local_host', return_value=True):
                self.assertEqual(client.get('/metrics').status_code, 401)
                with patch('app.main.simulator_token_valid', return_value=True):
                    response = client.get('/metrics', headers={'X-Friday-Simulator-Token': 'a' * 64})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers['content-type'].startswith('text/plain'))
                body = response.text
                for name in ('friday_up 1', 'friday_background_workers_healthy 1', 'friday_connected_devices 0',
                             'friday_database_available 1', 'friday_backup_enabled', '# TYPE friday_uptime_seconds gauge'):
                    self.assertIn(name, body)
                with patch('app.main.admin_token_valid', return_value=True):
                    diagnostics = client.get('/api/diagnostics', headers={'X-Friday-Admin-Token': 'a' * 64}).json()
                self.assertIn('consecutive_failures', diagnostics['backup'])
            names = {task.get_name() for task in app.state.background_tasks}
            self.assertIn('friday-worker-watchdog', names)


class ConfigurationTests(unittest.TestCase):
    def test_backup_settings_have_bounds(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.assistant_backup_interval_seconds, 86400.0)
        self.assertEqual(settings.assistant_backup_directory, Path('data/backups'))
        from pydantic import ValidationError
        for changes in ({'assistant_backup_interval_seconds': -1}, {'assistant_backup_keep_count': 0},
                        {'assistant_alert_cooldown_seconds': 1},
                        {'assistant_backup_directory': 'data/friday.db'}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Settings(_env_file=None, **changes)


class HealthProbeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script('check_backend_health.py')
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'state.json'
        self.ca = Path(self.temp.name) / 'ca.crt'
        self.ca.write_text('placeholder')

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, ok, kickstart_after=0):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *exc): return False
        side_effect = (lambda *a, **k: Response()) if ok else (lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError('refused')))
        with patch.object(self.module.ssl, 'create_default_context', return_value=None), \
             patch.object(self.module.urllib.request, 'urlopen', side_effect=side_effect), \
             patch.object(self.module, 'kickstart', return_value=True) as restart:
            code = self.module.main(['--state', str(self.state), '--ca', str(self.ca),
                                     '--kickstart-after', str(kickstart_after)])
        return code, restart

    def test_ready_resets_failures_and_exits_zero(self):
        self.state.write_text('{"failures": 5}')
        code, restart = self.run_main(ok=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.module.read_state(self.state), {'failures': 0})
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        restart.assert_not_called()

    def test_kickstart_only_after_consecutive_failures(self):
        code, restart = self.run_main(ok=False, kickstart_after=2)
        self.assertEqual(code, 1)
        restart.assert_not_called()
        self.assertEqual(self.module.read_state(self.state), {'failures': 1})
        code, restart = self.run_main(ok=False, kickstart_after=2)
        self.assertEqual(code, 1)
        restart.assert_called_once_with('com.friday.backend')
        self.assertEqual(self.module.read_state(self.state), {'failures': 0})

    def test_without_kickstart_failures_only_accumulate(self):
        for expected in (1, 2, 3):
            code, restart = self.run_main(ok=False)
            self.assertEqual(code, 1)
            restart.assert_not_called()
            self.assertEqual(self.module.read_state(self.state)['failures'], expected)


class HealthAgentDefinitionTests(unittest.TestCase):
    def test_health_agent_definition_is_bounded_and_secret_free(self):
        module = load_script('macos_service.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('.venv/bin/python', 'scripts/check_backend_health.py', 'data/tls/friday-ca.crt'):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text('')
            definition = module.health_definition(root, 120, kickstart_after=3)
            self.assertEqual(definition['Label'], 'com.friday.healthcheck')
            self.assertEqual(definition['StartInterval'], 120)
            self.assertEqual(definition['ProgramArguments'][2:], ['--kickstart-after', '3', '--label', 'com.friday.backend'])
            self.assertNotIn('EnvironmentVariables', definition)
            plain = module.health_definition(root)
            self.assertEqual(len(plain['ProgramArguments']), 2)
            for bad in ({'interval_seconds': 5}, {'interval_seconds': 7200}, {'kickstart_after': -1}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    module.health_definition(root, **bad)
            destination = root / 'health.plist'
            module.write_health_definition(root, destination)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                module.write_health_definition(root, destination)


if __name__ == '__main__':
    unittest.main()
