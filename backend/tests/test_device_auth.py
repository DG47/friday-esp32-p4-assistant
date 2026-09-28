import tempfile
import unittest
from pathlib import Path

from app.device_auth import DeviceCredentialStore


class DeviceCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = DeviceCredentialStore(Path(self.temp_dir.name) / "auth.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pairing_window_enrolls_one_device_then_verifies_it(self):
        token = "a" * 64
        self.store.open_pairing_window()

        self.assertEqual(self.store.authorize("friday-p3a", token), "paired")
        self.assertEqual(self.store.authorize("friday-p3a", token), "verified")
        self.assertFalse(self.store.pairing_open())

    def test_rejects_bad_token_and_unpaired_device(self):
        self.assertIsNone(self.store.authorize("friday-p3a", "too-short"))
        self.assertIsNone(self.store.authorize("friday-p3a", "b" * 64))

    def test_rejects_changed_token_after_pairing(self):
        self.store.open_pairing_window()
        self.assertEqual(self.store.authorize("friday-p3a", "c" * 64), "paired")
        self.assertIsNone(self.store.authorize("friday-p3a", "d" * 64))

    def test_revoke_removes_paired_identity(self):
        self.store.open_pairing_window()
        self.assertEqual(self.store.authorize("friday-p3a", "e" * 64), "paired")
        self.assertTrue(self.store.is_paired("friday-p3a"))
        self.assertTrue(self.store.revoke("friday-p3a"))
        self.assertFalse(self.store.is_paired("friday-p3a"))
        self.assertIsNone(self.store.authorize("friday-p3a", "e" * 64))


if __name__ == "__main__":
    unittest.main()
