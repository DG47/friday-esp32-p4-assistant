"""Synthetic online backup, distinct restore and stale-session fencing."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.worker_contract import WorkerContractStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/backup_database.py'
if not SCRIPT.is_file():
    SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/backup_database.py'
spec = importlib.util.spec_from_file_location('deployment_backup', SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class DeploymentRestoreTests(unittest.TestCase):
    def test_online_backup_restore_invalidates_session_even_if_generation_collides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkerContractStore(root / 'original.db')
            store.acquire()
            try:
                old = store.hello('synthetic-owner', 'synthetic-worker')
                backup.backup_database(store.path, root / 'snapshot.db')
                current = store.hello('synthetic-owner', 'synthetic-worker')
                self.assertEqual((root / 'snapshot.db').stat().st_mode & 0o777, 0o600)
                with self.assertRaises(FileExistsError):
                    backup.backup_database(store.path, root / 'snapshot.db')
            finally:
                store.release()
            # Restore into a NEW path; never overwrite a running service database.
            backup.backup_database(root / 'snapshot.db', root / 'restored.db')
            restored = WorkerContractStore(root / 'restored.db')
            restored.acquire()
            try:
                self.assertEqual(restored.status('synthetic-owner', 'synthetic-worker')['state'], 'offline')
                with self.assertRaises(ValueError):
                    restored.heartbeat('synthetic-owner', 'synthetic-worker', current['generation'], current['coordinator_epoch'])
                fresh = restored.hello('synthetic-owner', 'synthetic-worker')
                self.assertNotEqual(fresh['coordinator_epoch'], old['coordinator_epoch'])
                self.assertEqual(restored.heartbeat('synthetic-owner', 'synthetic-worker', fresh['generation'], fresh['coordinator_epoch'])['state'], 'connected')
            finally:
                restored.release()
            with closing(sqlite3.connect(root / 'restored.db')) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_corrupt_source_removes_incomplete_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'corrupt.db').write_bytes(b'Original synthetic corrupt database')
            with self.assertRaises(sqlite3.DatabaseError):
                backup.backup_database(root / 'corrupt.db', root / 'snapshot.db')
            self.assertFalse((root / 'snapshot.db').exists())
