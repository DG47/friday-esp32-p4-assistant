import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/macos_service.py'
spec = importlib.util.spec_from_file_location('macos_service', SCRIPT)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class MacServiceTests(unittest.TestCase):
    def test_definition_is_foreground_secret_free_and_provider_opt_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('.venv/bin/python', 'scripts/run_backend.py',
                         'data/tls/friday-backend.crt', 'data/tls/friday-backend.key'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch()
            destination = root / 'service.plist'
            service.write_definition(root, destination)
            value = plistlib.loads(destination.read_bytes())
            self.assertTrue(value['RunAtLoad'])
            self.assertTrue(value['KeepAlive'])
            self.assertEqual(value['ThrottleInterval'], 15)
            self.assertEqual(value['EnvironmentVariables']['TTS_PROVIDER'], 'none')
            self.assertEqual(value['EnvironmentVariables']['WEATHER_PROVIDER'], 'none')
            self.assertNotIn('--reload', value['ProgramArguments'])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                service.write_definition(root, destination)

    def test_missing_runtime_fails_before_creating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'service.plist'
            with self.assertRaises(ValueError):
                service.write_definition(root, destination)
            self.assertFalse(destination.exists())

class BoundedLoggingTests(unittest.TestCase):
    def test_service_log_rotates_and_keeps_private_permissions(self):
        import logging
        import os
        script = SCRIPT.with_name('run_backend.py')
        log_spec = importlib.util.spec_from_file_location('run_backend', script)
        module = importlib.util.module_from_spec(log_spec)
        log_spec.loader.exec_module(module)
        saved = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).level,
                        logging.getLogger(name).propagate) for name in ('uvicorn', 'uvicorn.error')}
        previous_umask = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                module.configure_logging(root)
                logger = logging.getLogger('uvicorn.error')
                for _ in range(10000):
                    logger.info('Local synthetic rotation probe ' + 'x' * 256)
                for handler in logger.handlers:
                    handler.flush()
                files = list((root / 'backend/data/logs').glob('backend-service.log*'))
                self.assertGreater(len(files), 1)
                self.assertLessEqual(len(files), 4)
                for file in files:
                    self.assertLessEqual(file.stat().st_size, 2 * 1024 * 1024 + 512)
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                for handler in logger.handlers:
                    handler.close()
        finally:
            os.umask(previous_umask)
            for name, (handlers, level, propagate) in saved.items():
                logger = logging.getLogger(name)
                logger.handlers, logger.level, logger.propagate = handlers, level, propagate
