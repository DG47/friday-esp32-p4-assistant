#!/usr/bin/env python3
"""Actual image validation with synthetic data, named volumes and no public port.

Never builds/pulls, reads .env, invokes real providers or touches other containers.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid


class DockerFailure(RuntimeError):
    def __init__(self, action, result):
        super().__init__('Isolated Docker command failed: ' + action)
        self.action, self.stdout, self.stderr = action, result.stdout, result.stderr


def docker(*arguments, timeout=40):
    result = subprocess.run(['docker', *arguments], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise DockerFailure(arguments[0], result)
    return result.stdout.strip()


def validate(image, root, evidence):
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix = 'friday-deployment-' + uuid.uuid4().hex[:12]
    data, fixtures, prep = prefix + '-data', prefix + '-fixtures', prefix + '-prep'
    names = []
    volumes = []
    metadata = json.loads(docker('image', 'inspect', image))[0]
    # Use immutable image ID for every test, regardless of later tag changes.
    image = metadata['Id']
    report = {'image_id': image, 'architecture': metadata['Architecture'], 'checks': {}}
    try:
        for volume in (data, fixtures):
            volumes.append(volume)
            docker('volume', 'create', '--label', 'friday-purpose=deployment-validation', '--label', 'friday-run=' + prefix, volume)
        names.append(prep)
        docker('run', '-d', '--name', prep, '--label', 'friday-run=' + prefix, '--network', 'none', '--cpus', '1', '--memory', '2g', '--pids-limit', '128', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--mount', 'type=volume,src=' + fixtures + ',dst=/scripts',
               image, 'python', '-c', 'import time; time.sleep(600)')
        fixture_scripts = ('backup_database.py', 'deployment_smoke.py', 'macos_service.py', 'connect_spotify.py', 'connect_google_calendar.py', 'recover_memory.py', 'run_backend.py')
        for script in fixture_scripts:
            docker('cp', str(root / 'scripts' / script), prep + ':/scripts/' + script)
        docker('rm', '-f', prep)
        names.remove(prep)

        common = ['--network', 'none', '--cpus', '1', '--memory', '2g', '--pids-limit', '128', '--read-only', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--user', '10001:10001',
                  '--tmpfs', '/tmp:rw,size=134217728,mode=1777',
                  '--mount', 'type=volume,src=' + data + ',dst=/app/data',
                  '--mount', 'type=volume,src=' + fixtures + ',dst=/scripts,readonly']
        environment = {'ASSISTANT_ENV': 'test', 'ASSISTANT_DATABASE_PATH': '/app/data/synthetic.db',
                       'ASSISTANT_PROVIDER': 'mock', 'STT_PROVIDER': 'none', 'TTS_PROVIDER': 'none',
                       'WEATHER_PROVIDER': 'none', 'MUSIC_PROVIDER': 'none', 'CALENDAR_PROVIDER': 'none',
                       'PYTHONPATH': '/app:/app/tests'}
        def env(provider='none'):
            values = environment | {'TTS_PROVIDER': provider}
            return [item for key, value in values.items() for item in ('--env', key + '=' + value)]
        def run(*command):
            name = prefix + '-phase-' + uuid.uuid4().hex[:8]
            names.append(name)
            result = docker('run', '--rm', '--name', name, '--label', 'friday-run=' + prefix, *common, *env(), image, *command, timeout=90)
            names.remove(name)
            return result

        source_paths = ['app/main.py', 'app/speech_provider.py', 'app/worker_contract.py', 'tests/test_deployment_speech.py', 'tests/test_deployment_restore.py', 'tests/test_deployment_process_smoke.py', 'tests/test_worker_contract.py', 'requirements.linux-arm64.lock',
                        'app/config.py', 'app/agent.py', 'app/agent_jobs.py', 'app/working_context.py',
                        'app/providers/router.py', 'app/providers/local_policy.py',
                        'app/providers/ollama_provider.py', 'tests/test_local_only.py']
        expected = {name: hashlib.sha256((root / 'backend' / name).read_bytes()).hexdigest() for name in source_paths}
        source_probe = "import hashlib,json;from pathlib import Path;print(json.dumps({name:hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest() for name in " + repr(source_paths) + "},sort_keys=True))"
        actual = json.loads(run('python', '-c', source_probe))
        if actual != expected:
            raise RuntimeError('Image source differs from isolated candidate')
        report['source_sha256'] = actual
        expected_fixtures = {name: hashlib.sha256((root / 'scripts' / name).read_bytes()).hexdigest() for name in fixture_scripts}
        fixture_probe = "import hashlib,json;from pathlib import Path;print(json.dumps({name:hashlib.sha256((Path('/scripts')/name).read_bytes()).hexdigest() for name in " + repr(fixture_scripts) + "},sort_keys=True))"
        actual_fixtures = json.loads(run('python', '-c', fixture_probe))
        if actual_fixtures != expected_fixtures:
            raise RuntimeError('Copied synthetic fixture scripts differ')
        report['fixture_sha256'] = actual_fixtures
        limits_probe = "import json;from pathlib import Path;p=Path('/sys/fs/cgroup');print(json.dumps({name:(p/name).read_text().strip() for name in ('cpu.max','memory.max','pids.max')}))"
        limits = json.loads(run('python', '-c', limits_probe))
        quota, period = limits['cpu.max'].split()
        if quota == 'max' or int(quota) > int(period) or limits['memory.max'] != '2147483648' or limits['pids.max'] != '128':
            raise RuntimeError('Container resource limits not enforced')
        report['resource_limits'] = limits

        probe = """import os,pathlib
assert os.getuid()==10001
p=pathlib.Path('/app/data/permission-proof');p.touch(mode=0o600);p.write_text('Original synthetic sentinel')
try:pathlib.Path('/app/root-write-proof').write_text('synthetic')
except OSError:pass
else:raise AssertionError('root filesystem writable')
assert p.stat().st_mode & 0o777 == 0o600
print('permissions verified')"""
        run('python', '-c', probe)
        report['checks']['uid_readonly_root_writable_volume'] = 'pass'
        run('python', '-m', 'pip', 'check')
        run('python', '-c', 'import faster_whisper,av,onnxruntime; print("local speech dependencies import")')
        report['checks']['locked_dependencies_and_native_imports'] = 'pass'
        # Separate containers share only this test's private named volume.
        run('python', '/scripts/deployment_smoke.py', 'write', '--directory', '/app/data/container-proof')
        result = json.loads(run('python', '/scripts/deployment_smoke.py', 'verify', '--directory', '/app/data/container-proof'))
        assert result['persistence'] == result['restore'] == 'verified'
        report['checks']['cross_container_persistence_backup_restore'] = 'pass'
        run('python', '-m', 'unittest', 'test_deployment_speech', 'test_deployment_restore',
            'test_deployment_process_smoke', 'test_worker_contract', '-v')
        report['checks']['deployment_regressions'] = 'pass'
        backend_suite = "import json,sys,unittest; r=unittest.TextTestRunner(stream=sys.stdout).run(unittest.defaultTestLoader.discover('/app/tests')); print('FRIDAY_BACKEND_TESTS='+json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skipped':len(r.skipped)})); sys.exit(not r.wasSuccessful())"
        output = run('python', '-c', backend_suite)
        (evidence / 'full-backend-suite.log').write_text(output)
        summary = next(line for line in output.splitlines() if line.startswith('FRIDAY_BACKEND_TESTS='))
        report['checks']['full_backend_suite'] = json.loads(summary.split('=', 1)[1])

        probe = "import json,urllib.request,urllib.error;\nfor path in ('health','ready'):\n try:\n  r=urllib.request.urlopen('http://127.0.0.1:8000/'+path,timeout=2);print(json.dumps({'path':path,'status':r.status}))\n except urllib.error.HTTPError as e:print(json.dumps({'path':path,'status':e.code}))"
        for provider, expected in (('none', 200), ('macos', 503)):
            name = prefix + '-' + provider
            names.append(name)
            docker('run', '-d', '--init', '--name', name, '--label', 'friday-run=' + prefix, *common, *env(provider), image,
                   'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1',
                   '--no-proxy-headers', '--no-access-log', '--timeout-graceful-shutdown', '10')
            deadline = time.monotonic() + 25
            while True:
                try:
                    statuses = [json.loads(line) for line in docker('exec', name, 'python', '-c', probe).splitlines()]
                    if statuses == [{'path': 'health', 'status': 200}, {'path': 'ready', 'status': expected}]:
                        break
                except RuntimeError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('Synthetic container readiness deadline exceeded')
                time.sleep(.25)
            report['checks']['readiness_' + provider] = 'pass'
            started = time.monotonic()
            docker('stop', '--time', '15', name)
            state = json.loads(docker('inspect', '--format', '{{json .State}}', name))
            if state['ExitCode'] not in (0, 143) or state['OOMKilled']:
                raise RuntimeError('Synthetic container did not shut down cleanly: exit=' + str(state['ExitCode']))
            if 'Application shutdown complete.' not in docker('logs', name):
                # Uvicorn logs can be on stderr; read both without publishing payloads.
                result = subprocess.run(['docker', 'logs', name], capture_output=True, text=True, timeout=10)
                if 'Application shutdown complete.' not in result.stdout + result.stderr:
                    raise RuntimeError('Application drain completion not observed')
            report['checks']['shutdown_' + provider] = {'exit_code': state['ExitCode'], 'application_drain_observed': True, 'seconds': round(time.monotonic() - started, 2)}
            docker('rm', name)
            names.remove(name)
        report['production_configuration_or_voice_verified'] = False
        report['status'] = 'passed'
        return report
    except (DockerFailure, subprocess.TimeoutExpired) as error:
        def decoded(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        output = decoded(error.stdout) + '\n' + decoded(error.stderr)
        (evidence / 'synthetic-failure.log').write_text(output[:4 * 1024 * 1024])
        report.update(status='failed', failure_kind=(error.action if isinstance(error, DockerFailure) else 'timeout'), failure_log='synthetic-failure.log')
        return report
    finally:
        def cleanup(kind, name):
            label_args = (['inspect', '--format', '{{index .Config.Labels "friday-run"}}', name]
                          if kind == 'container' else ['volume', 'inspect', '--format', '{{index .Labels "friday-run"}}', name])
            remove_args = ['rm', '-f', name] if kind == 'container' else ['volume', 'rm', name]
            try:
                label = subprocess.run(['docker', *label_args], capture_output=True, text=True, timeout=10)
                if label.returncode:
                    return 'already_absent' if 'no such' in label.stderr.lower() else 'unverified'
                if label.stdout.strip() != prefix:
                    return 'ownership_mismatch'
                removed = subprocess.run(['docker', *remove_args], capture_output=True, timeout=10)
                return 'removed' if removed.returncode == 0 else 'failed'
            except (OSError, subprocess.TimeoutExpired):
                return 'unverified'
        cleanup_results = {name: cleanup('container', name) for name in names}
        cleanup_results.update({name: cleanup('volume', name) for name in volumes})
        report['owned_resource_cleanup'] = cleanup_results
        if any(state not in {'removed', 'already_absent'} for state in cleanup_results.values()):
            report['cleanup_status'] = 'unverified'
            if report.get('status') == 'passed':
                report.update(status='failed', failure_kind='cleanup_unverified')
        else:
            report['cleanup_status'] = 'verified'



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--evidence', type=Path, default=Path('/tmp/friday-deployment-container-diagnostics'))
    args = parser.parse_args()
    report = validate(args.image, args.root, args.evidence)
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report.get('status') == 'passed' else 1)
