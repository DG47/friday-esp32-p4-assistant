#!/usr/bin/env python3
"""Foreground Friday server for a supervisor, with bounded local operational logs."""
import logging
import logging.handlers
import os
from pathlib import Path
import sys


def configure_logging(root: Path) -> None:
    directory = root / 'backend/data/logs'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = logging.handlers.RotatingFileHandler(
        directory / 'backend-service.log', maxBytes=2 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)sZ %(levelname)s %(name)s %(message)s'))
    import time
    handler.formatter.converter = time.gmtime
    for name in ('uvicorn', 'uvicorn.error'):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    os.chdir(root)
    sys.path.insert(0, str(root / 'backend'))
    # These optional providers require a separate explicit setup/activation.
    os.environ['TTS_PROVIDER'] = 'none'
    os.environ['WEATHER_PROVIDER'] = 'none'
    configure_logging(root)
    try:
        import uvicorn
        uvicorn.run('app.main:app', host='0.0.0.0', port=8000,
                    ssl_certfile=str(root / 'data/tls/friday-backend.crt'),
                    ssl_keyfile=str(root / 'data/tls/friday-backend.key'),
                    ws_ping_timeout=60, timeout_graceful_shutdown=25,
                    reload=False, access_log=False, log_config=None)
    except Exception:
        # Do not log arbitrary exception values or configuration inputs.
        logging.getLogger('uvicorn.error').error('Backend startup failed; supervisor will retry')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
