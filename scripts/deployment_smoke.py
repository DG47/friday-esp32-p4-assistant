#!/usr/bin/env python3
"""Two-process synthetic persistence/restore proof. No live app or providers."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from app.worker_contract import WorkerContractStore
from backup_database import backup_database


def run(phase, directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    source, snapshot = directory / 'worker-contract-synthetic.db', directory / 'snapshot.db'
    if phase == 'write':
        if source.exists() or snapshot.exists():
            raise ValueError('Synthetic destination already exists; use a new smoke root')
        store = WorkerContractStore(source)
        store.acquire()
        try:
            session = store.hello('synthetic-owner', 'synthetic-worker')
            backup_database(source, snapshot)
            marker = directory / 'synthetic-session.json'
            marker.touch(mode=0o600)
            marker.write_text(json.dumps(session))
        finally:
            store.release()
        return {'phase': 'write', 'online_backup': 'verified', 'shutdown': 'fenced'}
    if not source.is_file() or not snapshot.is_file():
        raise ValueError('Write phase must succeed first')
    session = json.loads((directory / 'synthetic-session.json').read_text())
    def rows(database):
        with closing(sqlite3.connect(database)) as db:
            return db.execute('SELECT worker,owner,generation,deadline FROM worker_connections ORDER BY worker').fetchall()
    source_rows, snapshot_rows = rows(source), rows(snapshot)
    identity = ('synthetic-worker', 'synthetic-owner')
    if (len(source_rows) != 1 or len(snapshot_rows) != 1
            or source_rows[0][:2] != identity or snapshot_rows[0][:2] != identity
            or source_rows[0][2:] != (session['generation'] + 1, 0)
            or snapshot_rows[0][2] != session['generation'] or snapshot_rows[0][3] <= 0):
        raise ValueError('Synthetic persisted checkpoint missing or changed')
    restored = directory / 'restored.db'
    backup_database(snapshot, restored)
    if rows(restored) != snapshot_rows:
        raise ValueError('Synthetic restored checkpoint differs')
    store = WorkerContractStore(restored)
    store.acquire()
    try:
        if store.status('synthetic-owner', 'synthetic-worker')['state'] != 'offline':
            raise ValueError('Restored worker must remain offline')
        try:
            store.heartbeat('synthetic-owner', 'synthetic-worker', session['generation'], session['coordinator_epoch'])
        except ValueError:
            pass
        else:
            raise ValueError('Stale session survived restore')
        store.hello('synthetic-owner', 'synthetic-worker')
    finally:
        store.release()
    for database in (source, snapshot, restored):
        with closing(sqlite3.connect(database)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Synthetic database integrity failed')
    return {'phase': 'verify', 'persistence': 'verified', 'restore': 'verified', 'old_session': 'rejected'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['write', 'verify'])
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.phase, args.directory), sort_keys=True))
