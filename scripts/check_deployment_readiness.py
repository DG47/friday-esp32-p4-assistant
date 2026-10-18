#!/usr/bin/env python3
"""Secret-free structural Docker preflight; never pull/build/start a container."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def command(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=20)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def preflight(root):
    report = {'production_image_validated': False, 'cloud_ready': False}
    if not shutil.which('docker'):
        return report | {'blocker': 'docker_cli_unavailable'}
    with tempfile.TemporaryDirectory(prefix='friday-profile-check-') as directory:
        temporary = Path(directory)
        shutil.copyfile(root / 'compose.production.yaml', temporary / 'compose.yaml')
        # --no-env-resolution and a synthetic empty file exclude production env.
        (temporary / '.env').touch(mode=0o600)
        output = command(['docker', 'compose', '-f', str(temporary / 'compose.yaml'),
                          'config', '--no-env-resolution', '--format', 'json'])
    if output is None:
        return report | {'blocker': 'compose_configuration_unavailable'}
    service = json.loads(output)['services']['backend']
    argv = service.get('command', [])
    mounts = service.get('volumes', [])
    checks = {
        'single_process': '--workers' in argv and argv[argv.index('--workers') + 1] == '1',
        'nonroot': service.get('user') == '10001:10001',
        'readonly_root': service.get('read_only') is True,
        'persistent_data': any(m.get('type') == 'volume' and m.get('target') == '/app/data' for m in mounts),
        'tls_readonly': any(m.get('target') == '/app/tls' and m.get('read_only') for m in mounts),
        'healthcheck': bool(service.get('healthcheck')),
        'restart_policy': service.get('restart') == 'unless-stopped',
    }
    report['profile_checks'] = checks
    report['structural_profile_valid'] = all(checks.values())
    # Host-bound LAN ports require separate public-ingress design; do not label
    # the existing Compose profile a reviewed Internet-facing deployment.
    report['public_ingress_reviewed'] = False
    report['docker_daemon_available'] = command(['docker', 'version', '--format', '{{.Server.Version}}']) is not None
    report['python_base_cached'] = command(['docker', 'image', 'inspect', 'python:3.12-slim', '--format', '{{.Id}}']) is not None
    report['blocker'] = ('production_image_not_built_or_exercised' if report['python_base_cached']
                         else 'python_base_not_cached_no_download_performed')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(preflight(args.root), indent=2, sort_keys=True))
