"""Opt-in loopback fixture demo, separate from production app.main.
No phone login or remote-access claim: one fixed local demo owner only.
"""
import asyncio
import fcntl
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from .task_slice import TaskStore, TaskWorker


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action_id: str = Field(min_length=1, max_length=128)


class Start(Input):
    goal: str = Field(min_length=1, max_length=500)
    fixture: Literal['range-total', 'range-total-two'] | None = None


class Clarify(Input):
    revision: int = Field(ge=1)
    fixture: Literal['range-total', 'range-total-two']


class Approve(Input):
    revision: int = Field(ge=1)
    scope_digest: str = Field(min_length=64, max_length=64)
    nonce: str = Field(min_length=32, max_length=32)


class Review(Input):
    revision: int = Field(ge=1)


class Turn(Input):
    client: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=500)


def create_demo_app(path, *, enabled=False, owner='local-fixture-owner', clock=None):
    store = TaskStore(path, **({'clock': clock} if clock else {}))
    worker = TaskWorker(store, owner)
    active = {}

    @asynccontextmanager
    async def lifespan(app):
        lock = None
        if enabled:
            lock = open(str(path)+'.supervisor.lock', 'a')
            os.chmod(lock.name, 0o600)
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise RuntimeError('Only one fixture supervisor may own this demo database') from None
        wake = asyncio.Event()

        def finished(done, task_id):
            active.pop(task_id, None)
            if not done.cancelled():
                done.exception()  # Retrieve failure without inventing drain proof.
            wake.set()

        async def supervise():
            while True:
                # Register the change waiter before reading, avoiding a missed
                # request/commit between the snapshot and the next wait.
                changed = asyncio.create_task(store.wait_changed(timeout=30))
                await asyncio.sleep(0)
                try:
                    await store.io(store.recover, owner)
                    snapshot = await store.io(store.list, owner)
                    for task in snapshot:
                        if task['state'] == 'queued' and task['id'] not in active:
                            running = asyncio.create_task(worker.run(task['id']))
                            active[task['id']] = running
                            running.add_done_callback(lambda done, key=task['id']: finished(done, key))
                    deadlines = [task['lease_until'] for task in snapshot
                                 if task['state'] == 'running' and task['lease_until'] > store.clock()]
                    timeout = min(30, max(.001, min(deadlines)-store.clock())) if deadlines else 30
                    completed = asyncio.create_task(wake.wait())
                    try:
                        await asyncio.wait((changed, completed), timeout=timeout,
                                           return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        completed.cancel()
                        await asyncio.gather(completed, return_exceptions=True)
                        wake.clear()
                finally:
                    changed.cancel()
                    await asyncio.gather(changed, return_exceptions=True)
        supervisor = asyncio.create_task(supervise()) if enabled else None
        try: yield
        finally:
            if supervisor: supervisor.cancel()
            pending = list(active.values())
            for running in pending: running.cancel()
            await asyncio.gather(*([supervisor] if supervisor else []), *pending, return_exceptions=True)
            if lock: lock.close()

    app = FastAPI(lifespan=lifespan)
    app.state.task_store = store
    app.state.task_worker = worker

    @app.middleware('http')
    async def demo_boundary(request: Request, call_next):
        # No configuration/runtime mutation of production security. This demo
        # cannot be exposed remotely; setup/authentication is a later release.
        if not enabled: return JSONResponse({'detail': 'Fixture demo disabled'}, status_code=503)
        if (not request.client or request.client.host not in {'127.0.0.1', '::1'}
                or request.url.hostname not in {'127.0.0.1', 'localhost', '::1', 'testserver'}):
            return JSONResponse({'detail': 'Loopback fixture demo only'}, status_code=403)
        if request.method != 'GET':
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin demo action denied'}, status_code=403)
            if request.headers.get('x-friday-fixture') != '1':
                return JSONResponse({'detail': 'Explicit fixture request required'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    async def checked(operation):
        try: return await store.io(operation)
        except ValueError as error: raise HTTPException(409, str(error)) from None

    @app.get('/')
    async def index(): return FileResponse(Path(__file__).parent / 'static/task-phone.html')

    @app.get('/task-phone.js')
    async def js(): return FileResponse(Path(__file__).parent / 'static/task-phone.js')

    @app.get('/task-phone.css')
    async def css(): return FileResponse(Path(__file__).parent / 'static/task-phone.css')

    @app.get('/api/tasks')
    async def tasks(): return {'tasks': await store.io(store.list, owner), 'boundary': 'Original synthetic fixtures; no model/Mac execution/login/remote connectivity.'}

    @app.post('/api/tasks')
    async def start(body: Start): return await checked(lambda: store.create(owner, body.action_id, body.goal, body.fixture))

    @app.get('/api/tasks/{task_id}')
    async def task(task_id: str): return await checked(lambda: {**store.get(owner, task_id), 'conversation': store.turns(owner, task_id)})

    @app.post('/api/tasks/{task_id}/clarify')
    async def clarify(task_id: str, body: Clarify): return await checked(lambda: store.clarify(owner, task_id, body.action_id, body.revision, body.fixture))

    @app.post('/api/tasks/{task_id}/review')
    async def review(task_id: str, body: Review): return await checked(lambda: store.review(owner,task_id,body.action_id,body.revision))

    @app.post('/api/tasks/{task_id}/approve')
    async def approve(task_id: str, body: Approve): return await checked(lambda: store.approve(owner, task_id, body.action_id, body.revision, body.scope_digest, body.nonce))

    @app.post('/api/tasks/{task_id}/stop')
    async def stop(task_id: str, body: Input): return await checked(lambda: store.stop(owner, task_id, body.action_id))

    @app.post('/api/tasks/{task_id}/conversation')
    async def converse(task_id: str, body: Turn):
        if body.text.lower().strip() in {'stop', 'stop this task'}:
            stopped = await checked(lambda: store.stop(owner, task_id, body.action_id))
            return {'answer': 'Stop state: ' + stopped['state'] + '.', 'task': stopped}
        answer = await checked(lambda: store.conversation(owner, task_id, body.action_id, body.client, body.text))
        return {'answer': answer, 'task': await checked(lambda: store.get(owner, task_id))}

    return app
