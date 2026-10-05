import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.db import ReminderStore

class TouchCancellationTests(unittest.TestCase):
    def test_cancel_requires_current_owner_identity_status_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReminderStore(Path(directory) / 'fixture.db')
            future = datetime.now(timezone.utc) + timedelta(minutes=5)
            pending = store.request_confirmation('first', 'owner', 'request', [], 'Synthetic', future)
            self.assertFalse(store.cancel_pending_confirmation(pending.id, 'other'))
            self.assertFalse(store.cancel_pending_confirmation('different', 'owner'))
            self.assertEqual(store.pending_confirmation('owner').id, pending.id)
            self.assertTrue(store.cancel_pending_confirmation(pending.id, 'owner'))
            self.assertFalse(store.cancel_pending_confirmation(pending.id, 'owner'))
            newer = store.request_confirmation('newer', 'owner', 'newer-request', [], 'Newer', future)
            self.assertFalse(store.cancel_pending_confirmation(pending.id, 'owner'))
            self.assertEqual(store.pending_confirmation('owner').id, newer.id)
            self.assertTrue(store.claim_confirmation(newer.id, 'owner'))
            self.assertFalse(store.cancel_pending_confirmation(newer.id, 'owner'))
            expired = store.request_confirmation('expired', 'other', 'expired-request', [], 'Expired', datetime.now(timezone.utc) - timedelta(seconds=1))
            self.assertFalse(store.cancel_pending_confirmation(expired.id, 'other'))
