# Original synthetic fixture. This standalone contract is not a production worker.
import hashlib
from pathlib import Path
import time
from app.worker_contract import WorkerContractStore, create_worker_contract_app
store = WorkerContractStore('/app/data/offline-worker.db')
with store.db() as db:
    db.execute('CREATE TABLE IF NOT EXISTS synthetic_admissions (ordinal INTEGER PRIMARY KEY)')
original = store.hello

def delayed(*args):
    Path('/tmp/release-fixtures/entered').touch()
    time.sleep(.8)
    result = original(*args)
    with store.db() as db:
        db.execute('INSERT INTO synthetic_admissions DEFAULT VALUES')
    return result

store.hello = delayed
app = create_worker_contract_app(store, credentials={'synthetic-worker': ('synthetic-owner', hashlib.sha256(('c'*64).encode()).hexdigest())}, enabled=True)
