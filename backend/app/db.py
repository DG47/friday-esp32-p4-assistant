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
                        completed INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )

    def create(self, text: str, due_at: datetime) -> Reminder:
        created_at = datetime.now(tz=due_at.tzinfo)
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO reminders (text, due_at, created_at) VALUES (?, ?, ?)",
                    (text, due_at.isoformat(), created_at.isoformat()),
                )
                reminder_id = int(cursor.lastrowid)
        return Reminder(
            id=reminder_id,
            text=text,
            due_at=due_at,
            created_at=created_at,
        )

    def pending(self) -> list[Reminder]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE completed = 0 ORDER BY due_at"
            ).fetchall()
        return [
            Reminder(
                id=row["id"],
                text=row["text"],
                due_at=datetime.fromisoformat(row["due_at"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                completed=bool(row["completed"]),
            )
            for row in rows
        ]
