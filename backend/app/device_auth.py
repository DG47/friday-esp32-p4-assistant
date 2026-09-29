from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOKEN_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
PAIRING_CODE_PATTERN = re.compile(r"^[0-9a-fA-F]{8}$")


class DeviceCredentialStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS device_credentials (
                        device_id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL,
                        paired_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS device_pairing_window (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        expires_at TEXT NOT NULL,
                        expected_device_id TEXT NOT NULL,
                        pairing_code_hash TEXT NOT NULL
                    )
                    """
                )
                pairing_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(device_pairing_window)"
                    ).fetchall()
                }
                if "expected_device_id" not in pairing_columns:
                    connection.execute(
                        "ALTER TABLE device_pairing_window "
                        "ADD COLUMN expected_device_id TEXT"
                    )
                if "pairing_code_hash" not in pairing_columns:
                    connection.execute(
                        "ALTER TABLE device_pairing_window "
                        "ADD COLUMN pairing_code_hash TEXT"
                    )

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def _normalize_pairing_code(pairing_code: str) -> str:
        normalized = pairing_code.replace("-", "").replace(" ", "").lower()
        if not PAIRING_CODE_PATTERN.fullmatch(normalized):
            raise ValueError("Pairing requires the 8-character code shown on Friday")
        return normalized

    def open_pairing_window(
        self, device_id: str, pairing_code: str, seconds: int = 120
    ) -> datetime:
        if not device_id:
            raise ValueError("Pairing requires a device ID")
        code_hash = self._hash(self._normalize_pairing_code(pairing_code))
        duration = max(15, min(seconds, 600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO device_pairing_window "
                    "(singleton, expires_at, expected_device_id, pairing_code_hash) "
                    "VALUES (1, ?, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET "
                    "expires_at = excluded.expires_at, "
                    "expected_device_id = excluded.expected_device_id, "
                    "pairing_code_hash = excluded.pairing_code_hash",
                    (expires_at.isoformat(), device_id, code_hash),
                )
        return expires_at

    def pairing_open(
        self, device_id: str | None = None, now: datetime | None = None
    ) -> bool:
        check_time = now or datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT expires_at, expected_device_id, pairing_code_hash "
                "FROM device_pairing_window "
                "WHERE singleton = 1"
            ).fetchone()
        return (
            row is not None
            and bool(row["expected_device_id"])
            and bool(row["pairing_code_hash"])
            and datetime.fromisoformat(row["expires_at"]) > check_time
            and (device_id is None or row["expected_device_id"] == device_id)
        )

    def is_paired(self, device_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM device_credentials WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return row is not None

    def revoke(self, device_id: str) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM device_credentials WHERE device_id = ?",
                    (device_id,),
                )
        return cursor.rowcount == 1

    def authorize(self, device_id: str, token: str) -> str | None:
        if not TOKEN_PATTERN.fullmatch(token):
            return None
        token_hash = self._hash(token)
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT token_hash FROM device_credentials WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is not None:
                if not secrets.compare_digest(row["token_hash"], token_hash):
                    connection.rollback()
                    return None
                connection.execute(
                    "UPDATE device_credentials SET last_seen_at = ? WHERE device_id = ?",
                    (now.isoformat(), device_id),
                )
                connection.commit()
                return "verified"

            window = connection.execute(
                "SELECT expires_at, expected_device_id, pairing_code_hash "
                "FROM device_pairing_window "
                "WHERE singleton = 1"
            ).fetchone()
            if (
                window is None
                or window["expected_device_id"] != device_id
                or not window["pairing_code_hash"]
                or not secrets.compare_digest(
                    window["pairing_code_hash"], self._hash(token[:8].lower())
                )
                or datetime.fromisoformat(window["expires_at"]) <= now
            ):
                connection.rollback()
                return None
            connection.execute(
                "INSERT INTO device_credentials "
                "(device_id, token_hash, paired_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (device_id, token_hash, now.isoformat(), now.isoformat()),
            )
            connection.execute(
                "UPDATE device_pairing_window SET expires_at = ? WHERE singleton = 1",
                (now.isoformat(),),
            )
            connection.commit()
            return "paired"
