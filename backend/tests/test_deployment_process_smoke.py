import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import unittest


class DeploymentProcessSmokeTests(unittest.TestCase):
    def test_missing_snapshot_record_cannot_claim_verified_persistence(self):
        root = Path(__file__).resolve().parents[2]
        script = root / 'scripts/deployment_smoke.py'
        if not script.is_file():
            script = Path(__file__).resolve().parents[1] / 'scripts/deployment_smoke.py'
        with tempfile.TemporaryDirectory() as directory:
            environment = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(root / 'backend')}
            if not (root / 'backend').is_dir():
                environment['PYTHONPATH'] = '/app'
            written = subprocess.run([sys.executable, str(script), 'write', '--directory', directory],
                                     env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(written.returncode, 0, written.stderr)
            with sqlite3.connect(Path(directory) / 'snapshot.db') as database:
                database.execute('DELETE FROM worker_connections')
            verified = subprocess.run([sys.executable, str(script), 'verify', '--directory', directory],
                                      env=environment, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(verified.returncode, 0, verified.stdout)
            self.assertNotIn('"persistence": "verified"', verified.stdout)

    def test_two_processes_preserve_synthetic_storage_and_reject_restored_session(self):
        root = Path(__file__).resolve().parents[2]
        script = root / 'scripts/deployment_smoke.py'
        if not script.is_file():
            script = Path(__file__).resolve().parents[1] / 'scripts/deployment_smoke.py'
        with tempfile.TemporaryDirectory() as directory:
            environment = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(root / 'backend')}
            if not (root / 'backend').is_dir():
                environment['PYTHONPATH'] = '/app'
            for phase in ('write', 'verify'):
                result = subprocess.run([sys.executable, str(script), phase, '--directory', directory],
                                        env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['phase'], phase)
