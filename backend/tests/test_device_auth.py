import tempfile
import unittest
import sqlite3
from contextlib import closing
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
        self.store.open_pairing_window("friday-p3a", "aaaaaaaa")

        self.assertEqual(self.store.authorize("friday-p3a", token), "paired")
        self.assertEqual(self.store.authorize("friday-p3a", token), "verified")
        self.assertFalse(self.store.pairing_open())

    def test_rejects_bad_token_and_unpaired_device(self):
        self.assertIsNone(self.store.authorize("friday-p3a", "too-short"))
        self.assertIsNone(self.store.authorize("friday-p3a", "b" * 64))

    def test_rejects_changed_token_after_pairing(self):
        self.store.open_pairing_window("friday-p3a", "cccccccc")
        self.assertEqual(self.store.authorize("friday-p3a", "c" * 64), "paired")
        self.assertIsNone(self.store.authorize("friday-p3a", "d" * 64))

    def test_revoke_removes_paired_identity(self):
        self.store.open_pairing_window("friday-p3a", "eeeeeeee")
        self.assertEqual(self.store.authorize("friday-p3a", "e" * 64), "paired")
        self.assertTrue(self.store.is_paired("friday-p3a"))
        self.assertTrue(self.store.revoke("friday-p3a"))
        self.assertFalse(self.store.is_paired("friday-p3a"))
        self.assertIsNone(self.store.authorize("friday-p3a", "e" * 64))

    def test_pairing_window_rejects_a_different_device(self):
        self.store.open_pairing_window("friday-p3a", "aaaaaaaa")

        self.assertFalse(self.store.pairing_open("impostor"))
        self.assertIsNone(self.store.authorize("impostor", "f" * 64))
        self.assertEqual(self.store.authorize("friday-p3a", "a" * 64), "paired")

    def test_existing_pairing_table_migrates_to_device_bound_windows(self):
        legacy_path = Path(self.temp_dir.name) / "legacy-auth.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute(
                "CREATE TABLE device_pairing_window ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "expires_at TEXT NOT NULL)"
            )

        migrated = DeviceCredentialStore(legacy_path)
        migrated.open_pairing_window("friday-p3a", "aaaaaaaa")

        self.assertTrue(migrated.pairing_open("friday-p3a"))
        self.assertFalse(migrated.pairing_open("other-device"))

    def test_pairing_rejects_a_token_that_does_not_match_displayed_code(self):
        self.store.open_pairing_window("friday-p3a", "abcd-1234")

        self.assertIsNone(self.store.authorize("friday-p3a", "f" * 64))
        self.assertEqual(
            self.store.authorize("friday-p3a", "abcd1234" + "0" * 56), "paired"
        )

    def test_pairing_requires_an_eight_character_hex_code(self):
        with self.assertRaises(ValueError):
            self.store.open_pairing_window("friday-p3a", "1234")


if __name__ == "__main__":
    unittest.main()
