#!/usr/bin/env python3
"""Serial offline synthetic TLS/recovery probe. Never reads real configuration or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid


def command(*args, timeout=30):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent
    image = json.loads(command('docker', 'image', 'inspect', args.image))[0]['Id']
    name = 'friday-offline-release-' + uuid.uuid4().hex[:12]
    report = {'image_id': image, 'status': 'failed', 'live_changes': False}
    created = False
    try:
        with tempfile.TemporaryDirectory(prefix='friday-synthetic-tls-') as tmp:
            tmp = Path(tmp)
            command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(tmp/'synthetic.key'),
                    '-out', str(tmp/'synthetic.crt'), '-days', '1', '-subj', '/CN=Friday Offline Synthetic TLS',
                    '-addext', 'subjectAltName=DNS:localhost')
            for source, target in [('deployment_offline_fixture.py', 'probe.py'),
                                   ('deployment_offline_worker_fixture.py', 'worker_fixture.py')]:
                shutil.copyfile(root/source, tmp/target)
            report['fixture_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in tmp.iterdir() if p.suffix == '.py'}
            created = True  # Cleanup also covers a daemon success followed by CLI timeout.
            command('docker', 'run', '-d', '--name', name, '--label', 'friday-purpose=offline-release-validation', '--label', 'friday-run=' + name,
                    '--network', 'none', '--cpus', '1', '--memory', '2g', '--pids-limit', '128', '--read-only',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', '10001:10001',
                    '--tmpfs', '/tmp:rw,size=134217728,mode=1777', '--tmpfs', '/app/data:rw,size=67108864,uid=10001,gid=10001',
                    '--env', 'ASSISTANT_ENV=test', '--env', 'ASSISTANT_DATABASE_PATH=/app/data/synthetic.db',
                    '--env', 'ASSISTANT_PROVIDER=mock', '--env', 'STT_PROVIDER=none', '--env', 'TTS_PROVIDER=none',
                    '--env', 'WEATHER_PROVIDER=none', '--env', 'MUSIC_PROVIDER=none', '--env', 'CALENDAR_PROVIDER=none',
                    '--env', 'PYTHONPATH=/app:/tmp/release-fixtures', image, 'python', '-c', 'import time;time.sleep(300)')
            command('docker', 'exec', name, 'mkdir', '/tmp/release-fixtures')
            # Public fixture-only key is read by non-root test process; never retain/export it as evidence.
            for p in tmp.iterdir():
                subprocess.run(['docker', 'exec', '-i', name, 'python', '-c',
                                'import sys;from pathlib import Path;Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
                                '/tmp/release-fixtures/' + p.name], input=p.read_bytes(), check=True,
                               capture_output=True, timeout=15)
            # Directory belongs to test UID, so synthetic probe can write gates/logs.
            output = command('docker', 'exec', name, 'python', '/tmp/release-fixtures/probe.py', timeout=60)
            report.update(json.loads(output.splitlines()[-1]))
            for log in ('main-tls.log', 'worker-inflight.log', 'worker-recovery.log'):
                (args.evidence/log).write_text(command('docker', 'exec', name, 'cat', '/tmp/release-fixtures/' + log))
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            (args.evidence/'failure.log').write_text(error.stdout + error.stderr)
        raise
    finally:
        if created:
            try:
                state = subprocess.run(['docker', 'container', 'inspect', name], capture_output=True, text=True, timeout=10)
                if state.returncode == 0:
                    meta = json.loads(state.stdout)[0]
                    labels = meta['Config']['Labels']
                    if (labels.get('friday-purpose') != 'offline-release-validation'
                            or labels.get('friday-run') != name):
                        raise RuntimeError('Refusing cleanup of an unowned container')
                    command('docker', 'rm', '-f', name)
                    state = subprocess.run(['docker', 'container', 'inspect', name], capture_output=True, text=True, timeout=10)
                if state.returncode != 0 and 'No such container' in state.stderr:
                    report['cleanup_status'] = 'verified'
                else:
                    raise RuntimeError('Container cleanup not verified')
            except Exception as error:
                report['cleanup_status'] = 'unverified'
                report['cleanup_error'] = str(error)
                report['status'] = 'failed'
        (args.evidence/'offline-container-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, sort_keys=True))
    if report['status'] != 'passed':
        raise RuntimeError('Offline release validation failed; see report')


if __name__ == '__main__':
    main()
