from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
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

    def create(
        self, text: str, due_at: datetime, device_id: str = "friday-p3a"
    ) -> Reminder:
        created_at = datetime.now(tz=due_at.tzinfo)
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO reminders (text, due_at, created_at, device_id) "
                    "VALUES (?, ?, ?, ?)",
                    (text, due_at.isoformat(), created_at.isoformat(), device_id),
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
            comparison_now = (
                now.replace(tzinfo=reminder.due_at.tzinfo)
                if now.tzinfo is None
                else now.astimezone(reminder.due_at.tzinfo)
            )
            if reminder.due_at <= comparison_now:
                due.append(reminder)
        return due

    def mark_delivered(self, reminder_id: int, delivered_at: datetime) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE reminders SET delivered_at = ? "
                    "WHERE id = ? AND delivered_at IS NULL",
                    (delivered_at.isoformat(), reminder_id),
                )
        return cursor.rowcount == 1
