"""Disconnected coordinator contracts. No commands, executor or production mount.

Caller supplies preconfigured credential hashes. This module creates no account
credentials, performs no network requests and defaults to disabled. A connected
worker is not evidence that any work or speech capability succeeds.
"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import fcntl
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

IDENTITY = re.compile(r'[a-zA-Z0-9_-]{1,64}')
HASH = re.compile(r'[0-9a-f]{64}')


def serialized(operation):
    @wraps(operation)
    def call(self, *args, **kwargs):
        with self._mutex:
            return operation(self, *args, **kwargs)
    return call


class Hello(BaseModel):
    model_config = ConfigDict(extra='forbid')
    worker_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    protocol: Literal[1] = 1


class Session(Hello):
    generation: int = Field(ge=1, strict=True)
    coordinator_epoch: str = Field(pattern=r'^[0-9a-f]{32}$')


class WorkerContractStore:
    """Single coordinator ownership plus persisted, expiring connection fences."""
    def __init__(self, path, clock=time.time, lease_seconds=30):
        if not 1 <= lease_seconds <= 60:
            raise ValueError('Invalid worker lease')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.clock, self.lease_seconds, self.lock_fd = clock, lease_seconds, None
        self.epoch = None
        self._mutex = threading.RLock()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS worker_connections '
                       '(worker TEXT PRIMARY KEY, owner TEXT NOT NULL, generation INTEGER NOT NULL, deadline REAL NOT NULL)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self, *, write=True):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            if not write:
                db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @serialized
    def acquire(self, epoch=None):
        if self.lock_fd is not None:
            raise RuntimeError('Coordinator already owned')
        fd = os.open(str(self.path) + '.owner', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Restart is not evidence that any worker or child drained.
            with self.db() as db:
                db.execute('UPDATE worker_connections SET generation=generation+1, deadline=0')
        except BaseException:
            os.close(fd)
            raise
        self.lock_fd = fd
        self.epoch = epoch or uuid.uuid4().hex

    @serialized
    def release(self, expected_epoch=None):
        if expected_epoch is not None and self.epoch != expected_epoch:
            return
        if self.lock_fd is not None:
            with self.db() as db:
                db.execute('UPDATE worker_connections SET generation=generation+1, deadline=0')
            fd, self.lock_fd = self.lock_fd, None
            os.close(fd)
            self.epoch = None

    def _owned(self):
        if self.lock_fd is None:
            raise ValueError('Coordinator unavailable')

    @serialized
    def hello(self, owner, worker):
        self._owned()
        with self.db() as db:
            row = db.execute('SELECT owner,generation FROM worker_connections WHERE worker=?', (worker,)).fetchone()
            if row and row[0] != owner:
                raise ValueError('Worker binding changed')
            generation = row[1] + 1 if row else 1
            deadline = self.clock() + self.lease_seconds
            db.execute('INSERT INTO worker_connections VALUES(?,?,?,?) '
                       'ON CONFLICT(worker) DO UPDATE SET generation=excluded.generation,deadline=excluded.deadline',
                       (worker, owner, generation, deadline))
        return {'worker_id': worker, 'generation': generation, 'coordinator_epoch': self.epoch, 'lease_seconds': self.lease_seconds,
                'state': 'connected', 'execution': 'not_implemented'}

    def _current(self, db, owner, worker, generation, epoch):
        row = db.execute('SELECT generation,deadline FROM worker_connections WHERE worker=? AND owner=?', (worker, owner)).fetchone()
        if epoch != self.epoch or not row or row[0] != generation or row[1] <= self.clock():
            raise ValueError('Worker connection expired or replaced')

    @serialized
    def heartbeat(self, owner, worker, generation, epoch):
        self._owned()
        with self.db() as db:
            self._current(db, owner, worker, generation, epoch)
            db.execute('UPDATE worker_connections SET deadline=? WHERE worker=?',
                       (self.clock() + self.lease_seconds, worker))
        return {'state': 'connected', 'execution': 'not_implemented'}

    @serialized
    def disconnect(self, owner, worker, generation, epoch):
        self._owned()
        with self.db() as db:
            self._current(db, owner, worker, generation, epoch)
            db.execute('UPDATE worker_connections SET deadline=0,generation=generation+1 WHERE worker=?', (worker,))
        return {'state': 'offline', 'reason': 'mac_worker_offline', 'termination': 'unproven'}

    @serialized
    def status(self, owner, worker):
        self._owned()
        with self.db(write=False) as db:
            row = db.execute('SELECT deadline FROM worker_connections WHERE worker=? AND owner=?', (worker, owner)).fetchone()
        online = row is not None and row[0] > self.clock()
        return {'state': 'connected' if online else 'offline',
                'reason': 'heartbeat_current' if online else 'mac_worker_offline',
                'execution': 'not_implemented', 'termination': 'unproven'}


async def storage_io(operation, *args):
    """Drain an admitted thread operation before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except Exception:
                break
        # Retrieve failures to avoid unobserved thread-task exceptions.
        if not task.cancelled():
            task.exception()
        raise


def create_worker_contract_app(store, *, credentials=None, enabled=False):
    """credentials: {worker_id: (owner_id, existing_sha256_token_hash)}.

    No credential files/env are read. Disabled contracts accept no mutations.
    This app is not mounted in app.main and is not a production worker service.
    """
    if type(enabled) is not bool:
        raise ValueError("Worker activation must be explicit boolean")
    bindings = dict(credentials or {})
    if len(bindings) > 32:
        raise ValueError("Worker binding capacity exceeded")
    for worker, binding in bindings.items():
        if (not isinstance(worker, str) or not IDENTITY.fullmatch(worker)
                or not isinstance(binding, tuple) or len(binding) != 2
                or not isinstance(binding[0], str) or not IDENTITY.fullmatch(binding[0])
                or not isinstance(binding[1], str) or not HASH.fullmatch(binding[1])):
            raise ValueError('Invalid preconfigured worker binding')
    if enabled and not bindings:
        raise ValueError('Worker activation requires existing credential bindings')

    @asynccontextmanager
    async def lifespan(app):
        if enabled:
            acquisition_epoch = uuid.uuid4().hex
            try:
                await storage_io(store.acquire, acquisition_epoch)
            except asyncio.CancelledError:
                await storage_io(store.release, acquisition_epoch)
                raise
        try:
            yield
        finally:
            if enabled:
                await storage_io(store.release, acquisition_epoch)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(worker, authorization, request):
        if not enabled or store.lock_fd is None:
            raise HTTPException(503, 'Worker interface disabled or unavailable')
        if request.url.scheme != 'https':
            raise HTTPException(403, 'Worker transport requires TLS')
        binding = bindings.get(worker)
        token = authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
        if (not binding or not HASH.fullmatch(token)
                or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), binding[1])):
            raise HTTPException(401, 'Worker authentication unavailable')
        return binding[0]

    async def invoke(operation, *args):
        try:
            return await storage_io(operation, *args)
        except ValueError:
            raise HTTPException(409, 'Worker connection expired or replaced') from None

    @app.post('/internal/workers/hello')
    async def hello(body: Hello, request: Request, authorization: str | None = Header(default=None)):
        return await invoke(store.hello, authenticate(body.worker_id, authorization, request), body.worker_id)

    @app.post('/internal/workers/heartbeat')
    async def heartbeat(body: Session, request: Request, authorization: str | None = Header(default=None)):
        return await invoke(store.heartbeat, authenticate(body.worker_id, authorization, request), body.worker_id, body.generation, body.coordinator_epoch)

    @app.post('/internal/workers/disconnect')
    async def disconnect(body: Session, request: Request, authorization: str | None = Header(default=None)):
        return await invoke(store.disconnect, authenticate(body.worker_id, authorization, request), body.worker_id, body.generation, body.coordinator_epoch)

    @app.get('/internal/workers/{worker_id}/status')
    async def status(worker_id: str, request: Request, authorization: str | None = Header(default=None)):
        return await invoke(store.status, authenticate(worker_id, authorization, request), worker_id)

    return app
