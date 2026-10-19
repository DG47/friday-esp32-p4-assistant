"""Trusted fixed-fixture regression runner; NOT an untrusted-code sandbox.
Only registered source/test bytes run. Generated code and selected repositories
are unsupported and must never be passed to this allow-default profile.
"""
import asyncio
import difflib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from .task_slice import TaskStore, TaskWorker, STEPS, digest

BEFORE = 'def total(start, stop):\n    return sum(range(start, stop))\n'
AFTER = 'def total(start, stop):\n    return sum(range(start, stop + 1))\n'
TEST = '''import unittest
from range_total import total
class RangeTests(unittest.TestCase):
    def test_inclusive(self):
        self.assertEqual(total(1, 4), 10)
        self.assertEqual(total(2, 5), 14)
unittest.main()
'''
# Alarm lives in the child, so a supervisor crash cannot leave this trusted test
# running indefinitely. No imports from user environments/site packages.
BOOT = "import signal,sys,runpy; signal.alarm(3); sys.path.insert(0,'.'); runpy.run_path('test_fixture.py',run_name='__main__')"
SOURCE = {'range_total.py': BEFORE, 'test_fixture.py': TEST}


def file_hash(data): return hashlib.sha256(data).hexdigest()


class LocalTaskStore(TaskStore):
    def __init__(self, path, root, clock=None):
        self.root = Path(root).absolute()
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError('Fixture root must have no symlink components')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        super().__init__(path, **({'clock':clock} if clock else {}))

    def source_digest(self, fixture):
        if fixture != 'range-total': raise ValueError('Only local range fixture allowed')
        return digest(SOURCE)

    def executor_digest(self):
        return digest({'local':file_hash(Path(__file__).read_bytes()), 'store':super().executor_digest(),
                       'python':str(Path(sys.executable).resolve()), 'root':str(self.root)})

    def _scope(self, task, fixture):
        super()._scope(task,fixture)
        task['execution_kind']='registered-local'
        task['scope'].update(executor='registered-local-project-v1', commands=True,
            argv=[str(Path(sys.executable).resolve()),'-I','-S','-B','-c',BOOT],
            writes='Per-task disposable fixture only: range_total.py fixed replacement; task evidence SQLite',
            fixture_root=str(self.root), timeout_seconds=3, output_bytes=8192,
            sandbox='Allow-default defense in depth for TRUSTED fixture ONLY; unsafe for untrusted/generated code',
            untrusted_code=False, output_egress='Safe summaries only; raw output stays local',
            planner='Deterministic known failure and fixed patch; no model reasoning')
        task['scope_digest']=digest(task['scope'])

    def drained(self, owner, task_id, fence):
        super().drained(owner,task_id,fence)
        # Caller only invokes this after subprocess termination and wait().
        with self.db() as db:
            task=self._get(db,owner,task_id)
            if task['state']=='stopped' and task['fence']==fence:
                task['events'][-1]['message']='Stopped; local child process reaped before drain.'
                db.execute('UPDATE demo_tasks SET body=? WHERE id=? AND owner=?',(json.dumps(task),task_id,owner))


class LocalTaskWorker(TaskWorker):
    def __init__(self, store, owner, before_execute=None):
        super().__init__(store,owner,before_execute)
        self.processes={}

    def authorized(self, db, task):
        current=self.store._get(db,self.owner,task['id'])
        if (current['state']!='running' or current['generation']!=task['generation']
                or current['fence']!=task['fence'] or self.store.clock()>=min(current['lease_until'],current['grant_until'])
                or self.store.executor_digest()!=task['scope']['executor_digest']):
            raise ValueError('Local operation authority changed')
        return current

    def project(self, task_id):
        if not re.fullmatch('[0-9a-f]{32}',task_id): raise ValueError('Invalid task path')
        root=self.store.root
        if root.resolve()!=root or root.is_symlink(): raise ValueError('Fixture root changed')
        path=root/task_id
        if path.is_symlink() or path.resolve()!=path: raise ValueError('Fixture path escape')
        path.mkdir(mode=0o700,exist_ok=True)
        pending=path/'.patch-pending'
        if pending.exists() or pending.is_symlink():
            if pending.is_symlink() or pending.stat().st_size>len(AFTER.encode()):
                raise ValueError('Unexpected pending patch')
            if not AFTER.encode().startswith(pending.read_bytes()):
                raise ValueError('Unexpected pending patch')
            pending.unlink()  # Incomplete atomic replacement; safe fixed-byte replay.
        for name in SOURCE:
            p=path/name
            if p.is_symlink() or p.resolve()!=p: raise ValueError('Fixture file escape')
            if not p.exists():
                fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as f:f.write(SOURCE[name])
        if set(p.name for p in path.iterdir()) != set(SOURCE): raise ValueError('Unexpected fixture files')
        if (path/'test_fixture.py').read_text()!=TEST: raise ValueError('Test bytes changed')
        if (path/'range_total.py').read_text() not in {BEFORE,AFTER}: raise ValueError('Source bytes changed')
        return path

    def argv(self):
        return [str(Path(sys.executable).resolve()),'-I','-S','-B','-c',BOOT]

    def _launch(self,task,path):
        argv=self.argv()
        if argv!=task['scope']['argv']:raise ValueError('Arguments changed since approval')
        with self.store.db() as db:
            current=self.authorized(db,task)
            if current.get('process',{}).get('state')=='owned':
                raise ValueError('Prior process needs reconciliation')
            current['process']={'worker':self.id,'fence':task['fence'],'state':'launching'}
            self.store._save(db,self.owner,current,'Process launch intent persisted.','process')
        with self.store.db() as db:
            current=self.authorized(db,task)
            proc=subprocess.Popen(['/usr/bin/sandbox-exec','-p',self.profile(path),*argv],
                cwd=path,env={'LANG':'C','LC_ALL':'C'},stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,start_new_session=True)
            self.processes[task['id']]=proc
            current['process']={'pid':proc.pid,'worker':self.id,'fence':task['fence'],'state':'owned'}
            self.store._save(db,self.owner,current,'Registered fixture process admitted.','process')
        return proc

    def _reaped(self,task,proc):
        with self.store.db() as db:
            current=self.store._get(db,self.owner,task['id'])
            process=current.get('process',{})
            if process.get('pid')==proc.pid and process.get('worker')==self.id and process.get('fence')==task['fence']:
                process.update(state='reaped',exit_code=proc.returncode)
                self.store._save(db,self.owner,current,'Registered fixture process reaped.','process')

    def profile(self, path):
        # macOS launch needs additional system runtime reads. Personal/project
        # trees remain denied; this is not a general untrusted-project sandbox.
        def quoted(p): return json.dumps(str(p))
        return '(version 1)(allow default)(deny network*)(deny process-fork)(deny file-write*)(deny file-read* (subpath "/Users") (subpath "/Volumes") (subpath "/private/tmp") (subpath "/private/var/folders"))(allow file-read* (subpath '+quoted(path)+'))'

    async def command(self, task, path):
        argv=self.argv()
        output=bytearray();reason=None;proc=None;reader=None
        async def read():
            while True:
                chunk=await asyncio.to_thread(proc.stdout.read,1024)
                if not chunk:break
                output.extend(chunk)
                if len(output)>8192: raise ValueError('Output bound exceeded')
        try:
            proc=await self.store.io(self._launch,task,path)
            reader=asyncio.create_task(read())
            deadline=asyncio.get_running_loop().time()+4
            while proc.poll() is None:
                current=await self.store.io(self.store.get,self.owner,task['id'])
                if (current['state']!='running' or current['generation']!=task['generation']
                        or current['fence']!=task['fence']):
                    reason='cancelled';break
                if self.store.clock()>=min(current['lease_until'],current['grant_until']):
                    reason='cancelled';break
                if asyncio.get_running_loop().time()>=deadline:reason='timeout';break
                if reader.done() and reader.exception():reason='output_limit';break
                changed=asyncio.create_task(self.store.wait_change(self.owner,task['id'],current['revision'],timeout=max(.001,deadline-asyncio.get_running_loop().time())))
                try:
                    await asyncio.wait((reader,changed),timeout=max(.001,deadline-asyncio.get_running_loop().time()),return_when=asyncio.FIRST_COMPLETED)
                finally:
                    changed.cancel()
                    await asyncio.gather(changed,return_exceptions=True)
            if reason:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            await asyncio.to_thread(proc.wait)
            await reader
            if reason=='cancelled': raise asyncio.CancelledError()
            if reason: raise ValueError(reason)
            return {'argv':argv,'cwd':str(path),'exit_code':proc.returncode,
                    'output':output.decode('utf-8','replace'), 'output_bytes':len(output),
                    'sandbox':'allow-default profile; trusted fixed fixture only; unsafe for untrusted code', 'passed':proc.returncode==0}
        finally:
            proc=proc or self.processes.get(task['id'])
            if proc is not None:
                if proc.poll() is None:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                await asyncio.to_thread(proc.wait)
                if reader is not None:
                    if not reader.done():reader.cancel()
                    await asyncio.gather(reader,return_exceptions=True)
                proc.stdout.close()
                await self.store.io(self._reaped,task,proc)
                self.processes.pop(task['id'],None)

    def _prepare(self,task):
        with self.store.db() as db:
            self.authorized(db,task)
            return self.project(task['id'])

    async def execute(self, task):
        path=await self.store.io(self._prepare,task)
        step=STEPS[task['step']]
        output={'source_digest':task['scope']['original_digest']}
        source=await self.store.io((path/'range_total.py').read_text)
        if step=='inspect':output.update(summary='Inspected registered disposable Python project.',files=await self.store.io(lambda:{n:file_hash((path/n).read_bytes()) for n in SOURCE}))
        elif step in {'check','verify'}:
            result=await self.command(task,path)
            output.update(result,summary=('Local test passed.' if result['passed'] else 'Local test failed: '+str(result['exit_code'])))
            if step=='check' and (result['exit_code']!=1 or '6 != 10' not in result['output']):
                output['fatal']=True
            if step=='verify':output['checked_digest']=await self.store.io(lambda:file_hash((path/'range_total.py').read_bytes()))
        elif step=='diagnose':output.update(summary='Observed test assertion and known source: range excludes final value.',basis=task['evidence'][-1]['output'],planner='Deterministic fixed diagnosis')
        else:
            await self.store.io(self._patch,task,path,source)
            output.update(summary='Patched only disposable range_total.py to include final value.',
                patch=''.join(difflib.unified_diff(BEFORE.splitlines(True),AFTER.splitlines(True),fromfile='a/range_total.py',tofile='b/range_total.py')),
                before_digest=file_hash(BEFORE.encode()),after_digest=file_hash(AFTER.encode()))
        return output

    def _patch(self,task,path,source):
        with self.store.db() as db:
            self.authorized(db,task)
            if source==BEFORE:
                p=path/'.patch-pending'
                fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as f:
                    f.write(AFTER);f.flush();os.fsync(f.fileno())
                os.replace(p,path/'range_total.py')

    async def step(self, task_id):
        task=await self.store.io(self.store.claim,self.owner,task_id,self.id)
        if task is None:return False
        heartbeat=asyncio.create_task(self._heartbeat(task))
        try:
            if self.before_execute:await self.before_execute(task)
            current=await self.store.io(self.store.get,self.owner,task_id)
            if current['state']!='running' or current['generation']!=task['generation']:return False
            output=await self.execute(task)
            return await self.store.io(self.store.commit,self.owner,task_id,task['fence'],task['generation'],output)
        except asyncio.CancelledError:
            return False
        except Exception as error:
            await self.store.io(self.store.failed,self.owner,task_id,task['fence'],task['generation'],type(error).__name__)
            return False
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
            await self.store.io(self.store.drained,self.owner,task_id,task['fence'])
