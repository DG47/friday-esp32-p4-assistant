#!/usr/bin/env python3
"""Generate a secret-free user LaunchAgent; installation is a separate action."""
import argparse
import os
import plistlib
from pathlib import Path

LABEL = 'com.friday.backend'


def service_definition(root: Path, tts_provider='none') -> dict:
    if tts_provider not in {'none', 'macos'}:
        raise ValueError('Only disabled or local Mac speech may be selected')
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
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'TTS_PROVIDER': tts_provider, 'WEATHER_PROVIDER': 'none'},
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': '/dev/null',
    }


def write_definition(root: Path, destination: Path, tts_provider='none') -> None:
    data = plistlib.dumps(service_definition(root, tts_provider))
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tts-provider', choices=['none', 'macos'], default='none')
    args = parser.parse_args()
    write_definition(args.root, args.output, args.tts_provider)
    print('User LaunchAgent definition written; credentials remain in existing local configuration.')
