from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import Reminder


class ReminderStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text TEXT NOT NULL,
                        due_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        device_id TEXT NOT NULL DEFAULT 'friday-p3a',
                        delivered_at TEXT,
                        delivery_attempted_at TEXT,
                        delivery_attempts INTEGER NOT NULL DEFAULT 0,
                        idempotency_key TEXT,
                        completed INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(reminders)").fetchall()
                }
                if "device_id" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN device_id TEXT NOT NULL "
                        "DEFAULT 'friday-p3a'"
                    )
                if "delivered_at" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN delivered_at TEXT"
                    )
                if "delivery_attempted_at" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN delivery_attempted_at TEXT"
                    )
                if "delivery_attempts" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN delivery_attempts INTEGER "
                        "NOT NULL DEFAULT 0"
                    )
                if "idempotency_key" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN idempotency_key TEXT"
                    )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS reminders_idempotency_key "
                    "ON reminders(idempotency_key) WHERE idempotency_key IS NOT NULL"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        device_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(request_id, role)
                    )
                    """
                )

    def create(
        self,
        text: str,
        due_at: datetime,
        device_id: str = "friday-p3a",
        idempotency_key: str | None = None,
    ) -> Reminder:
        created_at = datetime.now(tz=due_at.tzinfo)
        with closing(self._connect()) as connection:
            with connection:
                if idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM reminders WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing is not None:
                        return self._from_row(existing)
                cursor = connection.execute(
                    "INSERT INTO reminders "
                    "(text, due_at, created_at, device_id, idempotency_key) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        text,
                        due_at.isoformat(),
                        created_at.isoformat(),
                        device_id,
                        idempotency_key,
                    ),
                )
                reminder_id = int(cursor.lastrowid)
        return Reminder(
            id=reminder_id,
            text=text,
            due_at=due_at,
            created_at=created_at,
            device_id=device_id,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            text=row["text"],
            due_at=datetime.fromisoformat(row["due_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            device_id=row["device_id"],
            delivered_at=(
                datetime.fromisoformat(row["delivered_at"])
                if row["delivered_at"]
                else None
            ),
            completed=bool(row["completed"]),
        )

    def pending(self, device_id: str | None = None) -> list[Reminder]:
        query = (
            "SELECT * FROM reminders "
            "WHERE completed = 0 AND delivered_at IS NULL"
        )
        parameters: tuple[str, ...] = ()
        if device_id is not None:
            query += " AND device_id = ?"
            parameters = (device_id,)
        query += " ORDER BY due_at"
        with closing(self._connect()) as connection:
            rows = connection.execute(
                query, parameters
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def due(self, now: datetime) -> list[Reminder]:
        due = []
        for reminder in self.pending():
            # Older databases and SQLite's datetime() function can contain
            # timezone-naive UTC values. Normalize both sides before comparing
            # so one legacy row cannot stop the scheduler loop.
            reminder_due_at = reminder.due_at
            if reminder_due_at.tzinfo is None:
                reminder_due_at = reminder_due_at.replace(tzinfo=timezone.utc)
            comparison_now = now
            if comparison_now.tzinfo is None:
                comparison_now = comparison_now.replace(tzinfo=timezone.utc)
            if reminder_due_at <= comparison_now.astimezone(timezone.utc):
                due.append(reminder)
        return due

    def claim_delivery(
        self,
        reminder_id: int,
        attempted_at: datetime,
        retry_seconds: float = 90.0,
        max_attempts: int = 10,
    ) -> bool:
        retry_before = datetime.fromtimestamp(
            attempted_at.timestamp() - retry_seconds, tz=attempted_at.tzinfo
        ).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE reminders SET delivery_attempted_at = ?, "
                    "delivery_attempts = delivery_attempts + 1 "
                    "WHERE id = ? AND delivered_at IS NULL "
                    "AND delivery_attempts < ? "
                    "AND (delivery_attempted_at IS NULL OR delivery_attempted_at <= ?)",
                    (attempted_at.isoformat(), reminder_id, max_attempts, retry_before),
                )
        return cursor.rowcount == 1

    def mark_delivered(
        self, reminder_id: int, device_id: str, delivered_at: datetime
    ) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE reminders SET delivered_at = ? "
                    "WHERE id = ? AND device_id = ? AND delivered_at IS NULL",
                    (delivered_at.isoformat(), reminder_id, device_id),
                )
        return cursor.rowcount == 1

    def conversation(self, device_id: str, limit: int = 10) -> list[dict[str, str]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT role, content FROM conversation_messages "
                "WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
        return [
            {"role": row["role"], "content": row["content"]}
            for row in reversed(rows)
        ]

    def append_exchange(
        self, device_id: str, request_id: str, user_text: str, assistant_text: str
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO conversation_messages "
                    "(device_id, request_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (device_id, request_id, "user", user_text, now),
                        (device_id, request_id, "assistant", assistant_text, now),
                    ],
                )
