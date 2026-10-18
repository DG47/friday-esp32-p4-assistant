"""Synthetic-only offline TLS and graceful recovery probe, run inside bounded image."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import signal
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request

BASE = Path('/tmp/release-fixtures')
CA = str(BASE / 'synthetic.crt')
KEY = str(BASE / 'synthetic.key')
CTX = ssl.create_default_context(cafile=CA)
HEADERS = {'Authorization': 'Bearer ' + 'c' * 64}
RESULT = {}


def request(path, body=None, context=CTX, host='localhost'):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request('https://' + host + ':9443' + path, data=data,
                                 headers=HEADERS | {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def start(module):
    proc = subprocess.Popen(['python', '-m', 'uvicorn', module, '--host', '127.0.0.1', '--port', '9443',
                             '--no-proxy-headers', '--no-access-log', '--ssl-certfile', CA,
                             '--ssl-keyfile', KEY, '--timeout-graceful-shutdown', '5'],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError('Server failed: ' + proc.stdout.read())
        try:
            request('/health' if module == 'app.main:app' else '/internal/workers/synthetic-worker/status')
            return proc
        except (OSError, urllib.error.URLError):
            time.sleep(.05)
    proc.kill()
    raise TimeoutError('Synthetic server startup')


def stop(proc):
    proc.send_signal(signal.SIGTERM)
    log, _ = proc.communicate(timeout=8)
    assert 'Application shutdown complete.' in log, log
    assert proc.returncode in (0, -signal.SIGTERM), proc.returncode
    return log


async def websocket_probe():
    import websockets
    async with websockets.connect('wss://localhost:9443/ws/device/synthetic-device', ssl=CTX) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert hello.get('type') == 'hello.required', hello
        await ws.send(json.dumps({'type': 'scene.action', 'action': 'dismiss'}))
        packet = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert packet.get('type') == 'error', packet
        assert packet.get('code') == 'authentication_required', packet


def tls_and_shutdown():
    proc = start('app.main:app')
    try:
        status, health = request('/health')
        assert status == 200 and health == {'status': 'ok', 'mode': 'mock'}, health
        assert request('/ready')[0] == 200
        for label, context, host in [('untrusted', ssl.create_default_context(), 'localhost'),
                                     ('hostname_mismatch', CTX, '127.0.0.1')]:
            try:
                request('/health', context=context, host=host)
                raise AssertionError(label + ' unexpectedly trusted')
            except urllib.error.URLError as error:
                assert isinstance(error.reason, ssl.SSLCertVerificationError), repr(error)
        asyncio.run(websocket_probe())
    finally:
        (BASE / 'main-tls.log').write_text(stop(proc))
    RESULT['main_tls'] = {'trusted_https_health_ready': 'pass', 'untrusted_rejected': 'pass',
                          'hostname_mismatch_rejected': 'pass', 'wss_auth_required': 'pass'}

    proc = start('worker_fixture:app')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(request, '/internal/workers/hello', {'worker_id': 'synthetic-worker'})
            for _ in range(100):
                if (BASE / 'entered').exists():
                    break
                time.sleep(.01)
            assert (BASE / 'entered').exists()
            proc.send_signal(signal.SIGTERM)
            status, old = future.result(timeout=5)
            assert status == 200, old
            log, _ = proc.communicate(timeout=8)
            assert 'Application shutdown complete.' in log, log
            assert proc.returncode in (0, -signal.SIGTERM)
            (BASE / 'worker-inflight.log').write_text(log)
        with sqlite3.connect('/app/data/offline-worker.db') as db:
            assert db.execute('SELECT COUNT(*) FROM synthetic_admissions').fetchone()[0] == 1
    finally:
        if proc.poll() is None:
            stop(proc)
    proc = start('worker_fixture:app')
    try:
        state = request('/internal/workers/synthetic-worker/status')[1]
        assert state['state'] == 'offline' and state['termination'] == 'unproven'
        assert state['execution'] == 'not_implemented'
        session = {'worker_id': 'synthetic-worker', 'generation': old['generation'],
                   'coordinator_epoch': old['coordinator_epoch']}
        assert request('/internal/workers/heartbeat', session)[0] == 409
        status, fresh = request('/internal/workers/hello', {'worker_id': 'synthetic-worker'})
        assert status == 200 and fresh['generation'] > old['generation']
        assert fresh['coordinator_epoch'] != old['coordinator_epoch']
        with sqlite3.connect('/app/data/offline-worker.db') as db:
            assert db.execute('SELECT COUNT(*) FROM synthetic_admissions').fetchone()[0] == 2
    finally:
        (BASE / 'worker-recovery.log').write_text(stop(proc))
    RESULT['worker_inflight'] = {'admitted_tls_request_drained_once': 'pass', 'restart_offline_unproven': 'pass',
                                 'stale_session_rejected': 'pass', 'new_epoch_generation': 'pass',
                                 'real_execution': 'not_implemented'}


async def agent_recovery():
    from app.agent_jobs import AgentJobStore, AgentJobWorker
    from app.device_hub import DeviceHub
    from app.models import AgentReply, Surface
    store = AgentJobStore(Path('/app/data/offline-agent.db'))
    job = store.enqueue('synthetic-device', 'Original synthetic request', request_id='offline-synthetic-request')
    started = asyncio.Event()
    completed, cancelled = [], []
    class Blocked:
        async def handle(self, device_id, text, request_id=None):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(request_id)
                raise
    task = asyncio.create_task(AgentJobWorker(store, Blocked(), DeviceHub(), concurrency=1).run())
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    try:
        await asyncio.wait_for(task, 3)
    except asyncio.CancelledError:
        pass
    assert len(cancelled) == 1 and completed == []
    assert store.get(job.id).status == 'running'  # Durable lease, not falsely labelled cancelled/completed.
    # Simulate elapsed lease time in this private synthetic DB, without claiming real clock coverage.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job.id,))
    assert store.recover_interrupted() == 0
    assert store.get(job.id).error == 'interrupted_execution_outcome_uncertain'
    # A cancelled async handler is not proof that native/remote effects stopped.
    # Preserve the uncertain claim for reconciliation instead of replaying it.
    assert not await AgentJobWorker(store, Blocked(), DeviceHub(), concurrency=1).process_once()
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM agent_jobs').fetchone()[0] == 1
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    RESULT['agent_recovery'] = {'cooperative_inflight_cancel_observed': 'pass',
                              'durable_running_lease_no_false_completion': 'pass',
                              'uncertain_cancelled_claim_not_replayed': 'pass',
                              'real_external_effect_cancellation': 'unverified'}



def main():
    tls_and_shutdown()
    asyncio.run(agent_recovery())
    RESULT['resource_limits'] = {p: (Path('/sys/fs/cgroup') / p).read_text().strip()
                                 for p in ('cpu.max', 'memory.max', 'pids.max')}
    assert RESULT['resource_limits'] == {'cpu.max': '100000 100000', 'memory.max': '2147483648', 'pids.max': '128'}
    RESULT['status'] = 'passed'
    print(json.dumps(RESULT, sort_keys=True))


if __name__ == '__main__':
    main()
