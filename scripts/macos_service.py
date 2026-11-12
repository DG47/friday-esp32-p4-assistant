#!/usr/bin/env python3
"""Generate a secret-free user LaunchAgent; installation is a separate action."""
import argparse
import os
import plistlib
from pathlib import Path

LABEL = 'com.friday.backend'
HEALTH_LABEL = 'com.friday.healthcheck'


def service_definition(root: Path, tts_provider='none', weather_provider='none') -> dict:
    if tts_provider not in {'none', 'macos'}:
        raise ValueError('Only disabled or local Mac speech may be selected')
    if weather_provider not in {'none', 'open-meteo'}:
        raise ValueError('Choose a supported weather provider')
    root = root.resolve()
    for name in ('.venv/bin/python', 'scripts/run_backend.py',
                 'data/tls/friday-backend.crt', 'data/tls/friday-backend.key'):
        if not (root / name).is_file():
            raise ValueError(f'Required local file is missing: {name}')
    return {
        'Label': LABEL,
        'ProgramArguments': [str(root / '.venv/bin/python'), str(root / 'scripts/run_backend.py')],
        'WorkingDirectory': str(root),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 15,
        'ExitTimeOut': 30,
        'Umask': 0o077,
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'TTS_PROVIDER': tts_provider, 'WEATHER_PROVIDER': weather_provider},
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': '/dev/null',
    }


def health_definition(root: Path, interval_seconds: int = 60, kickstart_after: int = 0) -> dict:
    """Periodic readiness probe; optionally restarts the backend agent after repeated failures."""
    if not 15 <= interval_seconds <= 3600:
        raise ValueError('Health check interval must be between 15 and 3600 seconds')
    if kickstart_after < 0:
        raise ValueError('kickstart_after cannot be negative')
    root = root.resolve()
    for name in ('.venv/bin/python', 'scripts/check_backend_health.py', 'data/tls/friday-ca.crt'):
        if not (root / name).is_file():
            raise ValueError(f'Required local file is missing: {name}')
    arguments = [str(root / '.venv/bin/python'), str(root / 'scripts/check_backend_health.py')]
    if kickstart_after:
        arguments += ['--kickstart-after', str(kickstart_after), '--label', LABEL]
    return {
        'Label': HEALTH_LABEL,
        'ProgramArguments': arguments,
        'WorkingDirectory': str(root),
        'RunAtLoad': True,
        'StartInterval': interval_seconds,
        'Umask': 0o077,
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': '/dev/null',
    }


def _write(definition: dict, destination: Path) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(plistlib.dumps(definition))


def write_definition(root: Path, destination: Path, tts_provider='none', weather_provider='none') -> None:
    _write(service_definition(root, tts_provider, weather_provider), destination)


def write_health_definition(root: Path, destination: Path, interval_seconds: int = 60, kickstart_after: int = 0) -> None:
    _write(health_definition(root, interval_seconds, kickstart_after), destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, help='backend LaunchAgent plist to write')
    parser.add_argument('--tts-provider', choices=['none', 'macos'], default='none')
    parser.add_argument('--weather-provider', choices=['none', 'open-meteo'], default='none')
    parser.add_argument('--health-output', type=Path, help='health check LaunchAgent plist to write')
    parser.add_argument('--health-interval', type=int, default=60)
    parser.add_argument('--kickstart-after', type=int, default=0,
                        help='restart the backend after this many consecutive failed probes (0 never restarts)')
    args = parser.parse_args()
    if args.output is None and args.health_output is None:
        parser.error('choose --output, --health-output or both')
    if args.output is not None:
        write_definition(args.root, args.output, args.tts_provider, args.weather_provider)
        print('User LaunchAgent definition written; credentials remain in existing local configuration.')
    if args.health_output is not None:
        write_health_definition(args.root, args.health_output, args.health_interval, args.kickstart_after)
        print('Health check LaunchAgent definition written.')
