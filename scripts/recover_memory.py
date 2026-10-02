#!/usr/bin/env python3
"""Preview or recover one explicit memory revision. Run offline after a backup."""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from backup_database import backup_database


def recover(database, owner, memory_id, archived_revision, expected_revision=None, backup=None):
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        current=db.execute('SELECT * FROM explicit_memories WHERE owner=? AND id=?',(owner,memory_id)).fetchone()
        archived=db.execute('SELECT snapshot FROM memory_versions WHERE owner=? AND memory_id=? AND revision=?',(owner,memory_id,archived_revision)).fetchone()
        if current is None or archived is None:
            raise ValueError('Owned memory or archived revision unavailable')
        target=json.loads(archived['snapshot'])
        if target['owner'] != owner or target['id'] != memory_id:
            raise ValueError('Archived ownership mismatch')
        if not 1 <= len(target['text']) <= 500 or any(not 32 <= ord(character) <= 126 for character in target['text']):
            raise ValueError('Archived text cannot be reviewed with this device font or exceeds bounds')
        preview={'memory_id':memory_id,'owner':owner,'current_revision':current['revision'],'current_status':current['status'],'archived_revision':archived_revision,'restored_text':target['text']}
    if backup is None:
        return preview
    if expected_revision is None:
        raise ValueError('Applying recovery requires the reviewed current revision')
    backup_database(database,backup)
    with closing(sqlite3.connect(database)) as db, db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT * FROM explicit_memories WHERE owner=? AND id=? AND revision=?',(owner,memory_id,expected_revision)).fetchone()
        if current is None:
            raise ValueError('Memory changed; review a new preview')
        if current['status']=='forgotten' and db.execute("SELECT count(*) FROM explicit_memories WHERE owner=? AND status='active'",(owner,)).fetchone()[0]>=100:
            raise ValueError('100 active memory limit reached')
        archived=db.execute('SELECT snapshot FROM memory_versions WHERE owner=? AND memory_id=? AND revision=?',(owner,memory_id,archived_revision)).fetchone()
        if archived is None or json.loads(archived['snapshot']) != target:
            raise ValueError('Archived revision changed; review a new preview')
        timestamp=datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO memory_versions VALUES (?,?,?,?,?)',(memory_id,current['revision'],owner,json.dumps(dict(current)),timestamp))
        db.execute("UPDATE explicit_memories SET text=?,status='active',revision=revision+1,updated_at=? WHERE owner=? AND id=? AND revision=?",(target['text'],timestamp,owner,memory_id,expected_revision))
        db.execute("UPDATE memory_confirmations SET status='superseded' WHERE owner=? AND status='pending'",(owner,))
    preview['restored_revision']=expected_revision+1
    return preview


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    parser.add_argument('--owner',required=True)
    parser.add_argument('--memory-id',type=int,required=True)
    parser.add_argument('--archived-revision',type=int,required=True)
    parser.add_argument('--expected-current-revision',type=int)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    if args.apply and (args.backup is None or args.expected_current_revision is None):
        parser.error('--apply requires --backup and --expected-current-revision')
    if args.backup is not None and not args.apply:
        parser.error('--backup requires --apply; preview never writes')
    try:
        print(json.dumps(recover(args.database,args.owner,args.memory_id,args.archived_revision,args.expected_current_revision,args.backup if args.apply else None),indent=2))
    except (ValueError,sqlite3.Error,OSError) as error:
        raise SystemExit('Recovery unavailable: '+str(error))
