"""Fixture-only persistent task proof. No shell, model, network or filesystem tools.
Client labels are presentation metadata, NOT authentication. Owner is supplied by
an enclosing trusted demo/test application, never accepted from a request body.
"""
import asyncio
import hashlib
import json
import os
import secrets
import sqlite3
import time
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path

FIXTURES = {
    'range-total': {'start': 1, 'stop': 4, 'expected': 10, 'inclusive': False},
    'range-total-two': {'start': 2, 'stop': 5, 'expected': 14, 'inclusive': False},
}
STEPS = ('inspect', 'check', 'diagnose', 'prepare_patch', 'verify')
TERMINAL = {'ready_for_review', 'stopped', 'failed', 'uncertain'}
EXECUTOR_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def short(value, name, limit=128):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid ' + name)
    return value


class TaskStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._events = weakref.WeakKeyDictionary()
        self._event_lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection=sqlite3.connect(self.path)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
        finally:
            connection.close()
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS demo_tasks (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS demo_actions (
              owner TEXT NOT NULL, action_id TEXT NOT NULL, digest TEXT NOT NULL,
              task_id TEXT NOT NULL, result TEXT, PRIMARY KEY(owner,action_id));
            CREATE TABLE IF NOT EXISTS demo_turns (
              owner TEXT NOT NULL, turn_id TEXT NOT NULL, task_id TEXT NOT NULL,
              digest TEXT NOT NULL, client TEXT NOT NULL, text TEXT NOT NULL,
              answer TEXT NOT NULL, PRIMARY KEY(owner,turn_id));
            ''')
            for task_id, body in db.execute('SELECT id,body FROM demo_tasks').fetchall():
                task=json.loads(body)
                if 'created_at' not in task:
                    task.update(created_at=self.clock(),updated_at=self.clock())
                    db.execute('UPDATE demo_tasks SET body=? WHERE id=?',(json.dumps(task),task_id))

        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self, *, write=True):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            if not write: db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
            if write and db.total_changes: self._notify()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    async def io(self, operation, *args, **kwargs):
        """Finish admitted storage work off-loop even if its caller is cancelled."""
        job=asyncio.create_task(asyncio.to_thread(operation,*args,**kwargs))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            while not job.done():
                try: await asyncio.shield(job)
                except asyncio.CancelledError: continue
            # Retrieve errors; cancellation remains the caller-visible outcome.
            if not job.cancelled(): job.exception()
            raise

    def _event(self):
        loop=asyncio.get_running_loop()
        with self._event_lock:
            event=asyncio.Event()
            self._events.setdefault(loop,set()).add(event)
            return event

    def _release_event(self,event):
        with self._event_lock:
            self._events.get(asyncio.get_running_loop(),set()).discard(event)

    def _notify(self):
        with self._event_lock:
            listeners=[(loop,event) for loop,events in self._events.items() for event in events]
        for loop,event in listeners:
            if not loop.is_closed():
                try: loop.call_soon_threadsafe(event.set)
                except RuntimeError: pass

    async def wait_changed(self, timeout=30):
        event=self._event()
        try: await asyncio.wait_for(event.wait(),timeout)
        except TimeoutError: pass
        finally:self._release_event(event)

    async def wait_change(self, owner, task_id, revision, timeout=30):
        event=self._event()
        try:
            task=await self.io(self.get,owner,task_id)
            if task['revision']!=revision:return task
            try: await asyncio.wait_for(event.wait(),timeout)
            except TimeoutError: pass
        finally:self._release_event(event)
        return await self.io(self.get,owner,task_id)

    def _get(self, db, owner, task_id):
        row = db.execute('SELECT body FROM demo_tasks WHERE id=? AND owner=?', (task_id, owner)).fetchone()
        if not row:
            raise ValueError('Task unavailable')
        return json.loads(row[0])

    def _save(self, db, owner, task, message, kind='progress'):
        task['updated_at']=self.clock()
        task['revision'] += 1
        task['events'].append({'revision': task['revision'], 'kind': kind, 'message': message})
        task['events'] = task['events'][-100:]
        db.execute('UPDATE demo_tasks SET body=? WHERE id=? AND owner=?',
                   (json.dumps(task), task['id'], owner))

    def _action(self, db, owner, action_id, payload, task_id):
        short(action_id, 'action identity')
        hashed = digest(payload)
        row = db.execute('SELECT digest,task_id FROM demo_actions WHERE owner=? AND action_id=?', (owner, action_id)).fetchone()
        if row:
            if row != (hashed, task_id):
                raise ValueError('Action identity conflict')
            return True
        count=db.execute('SELECT COUNT(*) FROM demo_actions WHERE owner=?',(owner,)).fetchone()[0]
        if payload.get('op')!='stop' and count>=1000:
            raise ValueError('Demo action limit reached')
        if payload.get('op')=='stop' and count>=1032:
            return False  # Stop remains available without allocating another receipt.
        db.execute('INSERT INTO demo_actions(owner,action_id,digest,task_id) VALUES(?,?,?,?)',
                   (owner, action_id, hashed, task_id))
        return False

    def source_digest(self, fixture):
        return digest(FIXTURES[fixture])

    def executor_digest(self):
        return EXECUTOR_DIGEST

    def _scope(self, task, fixture):
        if fixture not in FIXTURES:
            raise ValueError('Only registered original synthetic fixtures are supported')
        task.update(fixture=fixture, state='awaiting_approval', nonce=secrets.token_hex(16),
                    expires_at=self.clock() + 300,
                    scope={'executor': 'trusted-fixture-arithmetic-v1', 'fixture': fixture,
                           'original_digest': self.source_digest(fixture), 'executor_digest': self.executor_digest(), 'operations': list(STEPS),
                           'writes': 'isolated SQLite task artifacts only', 'network': False,
                           'commands': False, 'models': False, 'publish': False, 'grant_seconds': 300})
        task['scope_digest'] = digest(task['scope'])

    def create(self, owner, action_id, goal, fixture=None):
        short(owner, 'owner'); short(goal, 'goal', 500)
        payload = {'op': 'start', 'goal': goal, 'fixture': fixture}
        self.prune(owner)
        with self.db() as db:
            old = db.execute('SELECT task_id,digest FROM demo_actions WHERE owner=? AND action_id=?', (owner, action_id)).fetchone()
            if old:
                if old[1] != digest(payload): raise ValueError('Action identity conflict')
                return self._get(db, owner, old[0])
            if db.execute('SELECT COUNT(*) FROM demo_tasks WHERE owner=?', (owner,)).fetchone()[0] >= 32:
                raise ValueError('Task capacity reached; existing tasks and Stop remain available')
            task = {'id': secrets.token_hex(16), 'goal': goal, 'state': 'awaiting_clarification',
                    'revision': 1, 'plan_revision': 1, 'step': 0, 'generation': 0,
                    'fence': 0, 'lease_until': 0, 'events': [], 'evidence': [], 'artifact': None,
                    'scope': None, 'scope_digest': None, 'nonce': None,
                    'created_at':self.clock(),'updated_at':self.clock(),'execution_kind':'arithmetic'}
            if fixture is not None: self._scope(task, fixture)
            task['events'].append({'revision': 1, 'kind': 'scope', 'message': 'Choose a synthetic fixture.' if fixture is None else 'Exact fixture-only scope awaits approval.'})
            db.execute('INSERT INTO demo_tasks VALUES(?,?,?)', (task['id'], owner, json.dumps(task)))
            self._action(db, owner, action_id, payload, task['id'])
            return task

    def get(self, owner, task_id):
        with self.db(write=False) as db: return self._get(db, owner, task_id)

    def list(self, owner):
        with self.db(write=False) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM demo_tasks WHERE owner=? ORDER BY rowid', (owner,))]

    def prune(self, owner, retention_seconds=7*86400):
        """Expire only old completed work; active/uncertain work is never removed.
        Action receipts for retained tasks stay authoritative. After task expiry,
        callers get unavailable; old task-scoped mutations cannot run again.
        """
        removed=0
        with self.db() as db:
            rows=db.execute('SELECT id,body FROM demo_tasks WHERE owner=?',(owner,)).fetchall()
            for task_id,body in rows:
                task=json.loads(body)
                if task.get('process',{}).get('state') in {'owned','launching'}:continue
                if task['state'] not in {'ready_for_review','stopped','failed'}:continue
                if self.clock()-task.get('updated_at',self.clock())<retention_seconds:continue
                # Pending transport receipts must be resolved before retirement.
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_outbox'").fetchone():
                    if db.execute("SELECT 1 FROM telegram_outbox WHERE status IN ('pending','sending','uncertain') AND (payload LIKE ? OR binding LIKE ?) LIMIT 1",('%'+task_id+'%','%'+task_id+'%')).fetchone():continue
                db.execute('DELETE FROM demo_turns WHERE owner=? AND task_id=?',(owner,task_id))
                # The documented retry window expires with completed work.
                # A replayed start becomes a NEW unapproved task; expired
                # task-scoped approval/patch actions can never execute again.
                db.execute('DELETE FROM demo_actions WHERE owner=? AND task_id=?',(owner,task_id))
                db.execute('DELETE FROM demo_tasks WHERE owner=? AND id=?',(owner,task_id));removed+=1
        return {'tasks':removed}

    def clarify(self, owner, task_id, action_id, revision, fixture):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if self._action(db, owner, action_id, {'op': 'clarify', 'task': task_id, 'revision': revision, 'fixture': fixture}, task_id): return task
            if task['state'] != 'awaiting_clarification' or task['revision'] != revision:
                raise ValueError('Clarification changed; refresh')
            self._scope(task, fixture)
            self._save(db, owner, task, 'Fixture selected; review exact scope.', 'scope')
            return task

    def review(self, owner, task_id, action_id, revision):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if self._action(db, owner, action_id, {'op':'review','task':task_id,'revision':revision},task_id): return task
            if task['state'] != 'awaiting_approval' or task['revision'] != revision:
                raise ValueError('Review changed; refresh')
            planner=task['scope'].get('planner') if isinstance(task['scope'].get('planner'),dict) else None
            if self.source_digest(task['fixture']) != task['scope']['original_digest'] or task['scope'].get('executor_digest') != self.executor_digest():
                planner=None
                task.update(step=0, evidence=[], artifact=None, plan_revision=task['plan_revision']+1)
            self._scope(task,task['fixture'])
            if planner:
                task['scope']['planner']=planner
                task['scope_digest']=digest(task['scope'])
            self._save(db,owner,task,'Exact scope refreshed; prior approval nonce invalidated.','scope')
            return task

    def approve(self, owner, task_id, action_id, revision, scope_digest, nonce):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if self._action(db, owner, action_id, {'op': 'approve', 'task': task_id, 'revision': revision, 'scope': scope_digest, 'nonce': nonce}, task_id): return task
            if (task['state'] != 'awaiting_approval' or task['revision'] != revision
                    or task['scope_digest'] != scope_digest or not isinstance(nonce, str)
                    or not secrets.compare_digest(task['nonce'] or '', nonce)
                    or self.clock() >= task['expires_at']):
                raise ValueError('Approval expired or changed; refresh exact scope')
            task.update(state='queued', nonce=None, approved_scope=scope_digest, grant_until=self.clock()+300)
            self._save(db, owner, task, 'Approved the displayed synthetic scope.', 'approval')
            return task

    def stop(self, owner, task_id, action_id):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if self._action(db, owner, action_id, {'op': 'stop', 'task': task_id}, task_id): return task
            if task['state'] in TERMINAL: return task
            task['generation'] += 1
            task['nonce'] = None
            # Running work must drain before claiming stopped. There are no child
            # processes in this executor; a later real runner needs kill evidence.
            task['state'] = 'stopping' if task['state'] in {'running', 'stopping'} else 'stopped'
            self._save(db, owner, task, 'Stop requested; no further steps admitted.', 'stop')
            return task

    def claim(self, owner, task_id, worker, lease_seconds=30):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if task['state'] != 'queued': return None
            if (self.source_digest(task['fixture']) != task['scope']['original_digest']
                    or task['scope'].get('executor_digest') != self.executor_digest()):
                task.update(step=0, evidence=[], artifact=None, plan_revision=task['plan_revision']+1)
                self._scope(task, task['fixture'])
                self._save(db, owner, task, 'Fixture or executor changed; new scope requires approval.', 'scope')
                return None
            if self.clock() >= task['grant_until']:
                task.update(state='awaiting_approval', nonce=secrets.token_hex(16), expires_at=self.clock()+300)
                self._save(db, owner, task, 'Execution grant expired; review exact scope to resume.', 'approval')
                return None
            task.update(state='running', fence=task['fence'] + 1,
                        worker=worker, lease_until=self.clock() + lease_seconds)
            self._save(db, owner, task, 'Running ' + STEPS[task['step']] + '.', 'progress')
            return task

    def heartbeat(self, owner, task_id, worker, fence, generation, lease_seconds=30):
        if not 1<=lease_seconds<=60:raise ValueError('Invalid lease duration')
        with self.db() as db:
            task=self._get(db,owner,task_id)
            if (task['state']!='running' or task.get('worker')!=worker or task['fence']!=fence
                    or task['generation']!=generation or self.clock()>=min(task['lease_until'],task['grant_until'])):return False
            task['lease_until']=min(self.clock()+lease_seconds,task['grant_until'])
            db.execute('UPDATE demo_tasks SET body=? WHERE owner=? AND id=?',(json.dumps(task),owner,task_id))
            return True

    def commit(self, owner, task_id, fence, generation, output):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if task['fence'] != fence or task['generation'] != generation or task['state'] != 'running': return False
            if self.clock() >= min(task['lease_until'], task['grant_until']): return False
            if (output.get('source_digest') != task['scope']['original_digest']
                    or self.source_digest(task['fixture']) != task['scope']['original_digest']
                    or task['scope'].get('executor_digest') != self.executor_digest()): return False
            step = STEPS[task['step']]
            task['evidence'].append({'step': step, 'output': output, 'digest': digest(output)})
            if step == 'prepare_patch': task['artifact'] = output
            task['step'] += 1
            task.update(state='ready_for_review' if task['step'] == len(STEPS) else 'queued', lease_until=0)
            if step == 'verify' and not output['passed']: task['state'] = 'failed'
            if output.get('fatal'): task['state'] = 'failed'
            self._save(db, owner, task, output['summary'], 'result' if task['state'] in TERMINAL else 'progress')
            return True

    def failed(self, owner, task_id, fence, generation, reason):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if (task['state'] == 'running' and task['fence'] == fence and task['generation'] == generation
                    and self.clock() < min(task['lease_until'], task['grant_until'])):
                task['state'] = 'failed'
                self._save(db, owner, task, 'Fixture operation failed: ' + reason + '.', 'error')

    def drained(self, owner, task_id, fence):
        with self.db() as db:
            task = self._get(db, owner, task_id)
            if task.get('process',{}).get('state') in {'owned','launching'}:return False
            if task['state'] == 'stopping' and task['fence'] == fence:
                task['state'] = 'stopped'
                self._save(db, owner, task, 'Stopped; fixture operation drained, no child processes exist.', 'stop')

    def recover(self, owner, *, drained_tasks=()):
        with self.db() as db:
            for row in db.execute('SELECT body FROM demo_tasks WHERE owner=?', (owner,)).fetchall():
                task = json.loads(row[0])
                unresolved=task.get('process',{}).get('state') in {'owned','launching'}
                if unresolved and task['state'] not in {'running','stopping','uncertain'}:
                    task['fence']+=1;task['state']='uncertain'
                    self._save(db,owner,task,'Process ownership requires reconciliation; no termination proof.','recovery')
                    continue
                if task['state'] in {'running', 'stopping'} and self.clock() >= task['lease_until']:
                    if task.get('execution_kind')=='registered-local' and task.get('process',{}).get('state') in {'owned','launching'}:
                        task['fence']+=1
                        task['state']='uncertain'
                        self._save(db,owner,task,'Process ownership requires reconciliation; no termination proof.','recovery')
                        continue
                    if task['state'] == 'stopping' and task['id'] not in drained_tasks:
                        continue  # A lease timeout alone is not termination evidence.
                    task['fence'] += 1
                    task['state'] = 'stopped' if task['state'] == 'stopping' else 'queued'
                    # Every registered operation is pure arithmetic / atomic DB
                    # commit: replay is safe. Real side effects must become uncertain.
                    self._save(db, owner, task, 'Recovered expired fixture lease; previous worker fenced.', 'recovery')

    def conversation(self, owner, task_id, turn_id, client, text):
        short(turn_id, 'turn identity'); short(client, 'client label', 40); short(text, 'message', 500)
        with self.db() as db:
            task = self._get(db, owner, task_id)
            row = db.execute('SELECT task_id,digest,answer FROM demo_turns WHERE owner=? AND turn_id=?', (owner, turn_id)).fetchone()
            if row:
                if row[:2] != (task_id, digest(text)): raise ValueError('Turn identity conflict')
                return row[2]
            if db.execute('SELECT COUNT(*) FROM demo_turns WHERE owner=?', (owner,)).fetchone()[0] >= 1000: raise ValueError('Demo conversation limit reached')
            normalized = text.lower().strip()
            if normalized in {'stop', 'stop this task'}:
                answer = 'Use the explicit Stop control; it acts without a model or approval.'
            elif 'fail' in normalized:
                result = next((e['output'] for e in task['evidence'] if e['step'] == 'check'), None)
                answer = result['summary'] if result else 'No failing check has been observed yet.'
            elif 'chang' in normalized or 'patch' in normalized:
                answer = task['artifact']['summary'] if task['artifact'] else 'No patch has been prepared yet. Original fixture remains unchanged.'
            elif 'status' in normalized or 'doing' in normalized or 'resume' in normalized:
                answer = task['events'][-1]['message'] + ' State: ' + task['state'] + '.'
            else:
                answer = 'This deterministic demo can explain status, the failing check and the patch. It has no general language model or Mac access.'
            db.execute('INSERT INTO demo_turns VALUES(?,?,?,?,?,?,?)', (owner, turn_id, task_id, digest(text), client, text, answer))
            return answer

    def turns(self, owner, task_id):
        with self.db(write=False) as db:
            self._get(db, owner, task_id)
            return [{'client': row[0], 'text': row[1], 'answer': row[2]} for row in db.execute('SELECT client,text,answer FROM demo_turns WHERE owner=? AND task_id=? ORDER BY rowid', (owner, task_id))]


def _execute_fixture(task):
    """Trusted fixed operations over registered values; never executes project code."""
    fixture = dict(FIXTURES[task['fixture']])
    original = digest(fixture)
    step = STEPS[task['step']]
    observed = sum(range(fixture['start'], fixture['stop']))
    if step == 'inspect': return {'summary': 'Inspected original synthetic range-total fixture.', 'original_digest': original}
    if step == 'check': return {'summary': f"Observed failing fixture check: expected {fixture['expected']}, got {observed}.", 'passed': False, 'expected': fixture['expected'], 'actual': observed, 'original_digest': original}
    if step == 'diagnose': return {'summary': 'The registered range operation excludes its final value.', 'basis': 'Observed expected/actual values and fixed fixture specification.', 'inference': 'Deterministic fixture diagnosis, not model reasoning.'}
    if step == 'prepare_patch':
        updated = {**fixture, 'inclusive': True}
        return {'summary': 'Prepared isolated fixture patch: include the final range value.', 'patch': '- "inclusive": false\n+ "inclusive": true\n', 'before_digest': original, 'after_digest': digest(updated), 'updated_fixture': updated}
    artifact = task['artifact']
    updated = artifact['updated_fixture']
    actual = sum(range(updated['start'], updated['stop'] + int(updated['inclusive'])))
    passed = actual == updated['expected'] and digest(updated) == artifact['after_digest'] and original == artifact['before_digest']
    return {'summary': 'Verified the isolated fixture patch; original unchanged. Ready for review.' if passed else 'Fixture verification failed.', 'passed': passed, 'actual': actual, 'expected': updated['expected'], 'checked_digest': digest(updated), 'original_digest': original}


def execute_fixture(task):
    approved = task['scope']['original_digest']
    if digest(FIXTURES[task['fixture']]) != approved:
        raise ValueError('Fixture changed since scope approval')
    output = _execute_fixture(task)
    output['source_digest'] = approved
    return output


class TaskWorker:
    def __init__(self, store, owner, before_execute=None):
        self.store, self.owner = store, owner
        self.id = secrets.token_hex(16)
        self.before_execute = before_execute

    async def _heartbeat(self, task):
        while True:
            await asyncio.sleep(5)
            renewed=await self.store.io(self.store.heartbeat,self.owner,task['id'],self.id,task['fence'],task['generation'])
            if not renewed:return

    async def step(self, task_id):
        task = await self.store.io(self.store.claim,self.owner,task_id,self.id)
        if task is None: return False
        heartbeat=asyncio.create_task(self._heartbeat(task))
        try:
            if self.before_execute: await self.before_execute(task)
            await asyncio.sleep(0)  # Permit cancellation/another client before work.
            current = await self.store.io(self.store.get,self.owner,task_id)
            if current['state'] != 'running' or current['generation'] != task['generation'] or current['fence'] != task['fence']: return False
            output = execute_fixture(task)
            return await self.store.io(self.store.commit,self.owner,task_id,task['fence'],task['generation'],output)
        except Exception as error:
            await self.store.io(self.store.failed,self.owner,task_id,task['fence'],task['generation'],type(error).__name__)
            return False
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
            await self.store.io(self.store.drained,self.owner,task_id,task['fence'])

    async def run(self, task_id):
        while await self.step(task_id):
            await asyncio.sleep(0)
