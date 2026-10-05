from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Reminder


SAFE_TOOL_CALL_ID = re.compile(r"^call_[0-9a-f]{24}$")


@dataclass
class PendingConfirmation:
    id: str
    device_id: str
    request_id: str
    actions: list[dict[str, Any]]
    summary: str
    expires_at: datetime
    status: str


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
                        completed INTEGER NOT NULL DEFAULT 0,
                        completed_at TEXT,
                        cancelled_at TEXT,
                        updated_at TEXT
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
                if "completed_at" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN completed_at TEXT"
                    )
                if "cancelled_at" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN cancelled_at TEXT"
                    )
                if "updated_at" not in columns:
                    connection.execute(
                        "ALTER TABLE reminders ADD COLUMN updated_at TEXT"
                    )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS reminders_idempotency_key "
                    "ON reminders(idempotency_key) WHERE idempotency_key IS NOT NULL"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reminder_actions (
                        idempotency_key TEXT PRIMARY KEY,
                        reminder_id INTEGER NOT NULL,
                        device_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conversation_table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'conversation_messages'"
                ).fetchone()
                if conversation_table is None:
                    self._create_conversation_table(connection)
                else:
                    conversation_columns = {
                        row["name"]
                        for row in connection.execute(
                            "PRAGMA table_info(conversation_messages)"
                        ).fetchall()
                    }
                    if "event_key" not in conversation_columns:
                        connection.execute(
                            "ALTER TABLE conversation_messages "
                            "RENAME TO conversation_messages_legacy"
                        )
                        self._create_conversation_table(connection)
                        connection.execute(
                            "INSERT INTO conversation_messages "
                            "(event_key, device_id, request_id, role, content, created_at) "
                            "SELECT 'legacy:' || id, device_id, request_id, role, "
                            "content, created_at FROM conversation_messages_legacy "
                            "ORDER BY id"
                        )
                        connection.execute("DROP TABLE conversation_messages_legacy")
                self._migrate_tool_call_ids(connection)
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pending_confirmations (
                        id TEXT PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        actions_json TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        claimed_at TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS conversation_messages_device_id "
                    "ON conversation_messages(device_id, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS pending_confirmations_device_status "
                    "ON pending_confirmations(device_id, status, created_at)"
                )

    @staticmethod
    def _create_conversation_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                device_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                name TEXT,
                tool_call_id TEXT,
                tool_calls_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _migrate_tool_call_ids(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT id, device_id, request_id, tool_call_id, tool_calls_json "
            "FROM conversation_messages "
            "WHERE tool_call_id IS NOT NULL OR tool_calls_json IS NOT NULL"
        ).fetchall()
        replacements: dict[tuple[str, str, str], str] = {}

        def safe_id(device_id: str, request_id: str, old_id: str) -> str:
            if SAFE_TOOL_CALL_ID.fullmatch(old_id):
                return old_id
            key = (device_id, request_id, old_id)
            if key not in replacements:
                digest = hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()[:24]
                replacements[key] = f"call_{digest}"
            return replacements[key]

        for row in rows:
            tool_call_id = row["tool_call_id"]
            migrated_tool_call_id = (
                safe_id(row["device_id"], row["request_id"], tool_call_id)
                if tool_call_id
                else None
            )
            tool_calls_json = row["tool_calls_json"]
            migrated_tool_calls_json = tool_calls_json
            if tool_calls_json:
                try:
                    tool_calls = json.loads(tool_calls_json)
                except (json.JSONDecodeError, TypeError):
                    tool_calls = None
                if isinstance(tool_calls, list):
                    changed = False
                    for call in tool_calls:
                        if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                            continue
                        migrated_id = safe_id(
                            row["device_id"], row["request_id"], call["id"]
                        )
                        if migrated_id != call["id"]:
                            call["id"] = migrated_id
                            changed = True
                    if changed:
                        migrated_tool_calls_json = json.dumps(tool_calls, sort_keys=True)
            if (
                migrated_tool_call_id != tool_call_id
                or migrated_tool_calls_json != tool_calls_json
            ):
                connection.execute(
                    "UPDATE conversation_messages SET tool_call_id = ?, "
                    "tool_calls_json = ? WHERE id = ?",
                    (migrated_tool_call_id, migrated_tool_calls_json, row["id"]),
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
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            cancelled_at=(
                datetime.fromisoformat(row["cancelled_at"])
                if row["cancelled_at"]
                else None
            ),
        )

    def active(self, device_id: str) -> list[Reminder]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM reminders WHERE device_id = ? "
                "AND completed = 0 AND cancelled_at IS NULL ORDER BY due_at, id",
                (device_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def active_by_id(self, reminder_id: int, device_id: str) -> Reminder | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ? AND device_id = ? "
                "AND completed = 0 AND cancelled_at IS NULL",
                (reminder_id, device_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def pending(self, device_id: str | None = None) -> list[Reminder]:
        query = (
            "SELECT * FROM reminders "
            "WHERE completed = 0 AND cancelled_at IS NULL AND delivered_at IS NULL"
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
                    "AND completed = 0 AND cancelled_at IS NULL "
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
                    "WHERE id = ? AND device_id = ? AND delivered_at IS NULL "
                    "AND completed = 0 AND cancelled_at IS NULL",
                    (delivered_at.isoformat(), reminder_id, device_id),
                )
        return cursor.rowcount == 1

    def mutate_reminder(
        self,
        reminder_id: int,
        device_id: str,
        action: str,
        idempotency_key: str,
        due_at: datetime | None = None,
    ) -> dict[str, Any]:
        if action not in {"cancel", "complete", "snooze"}:
            raise ValueError(f"Unsupported reminder action: {action}")
        now = datetime.now(timezone.utc)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT result_json FROM reminder_actions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return json.loads(existing["result_json"])

            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ? AND device_id = ?",
                (reminder_id, device_id),
            ).fetchone()
            if row is None:
                result: dict[str, Any] = {
                    "ok": False,
                    "error": "Reminder not found",
                    "reminder_id": reminder_id,
                }
            elif row["completed"] or row["cancelled_at"]:
                state = "completed" if row["completed"] else "cancelled"
                result = {
                    "ok": False,
                    "error": f"Reminder is already {state}",
                    "reminder_id": reminder_id,
                }
            elif action == "snooze" and due_at is None:
                result = {
                    "ok": False,
                    "error": "A new due time is required",
                    "reminder_id": reminder_id,
                }
            else:
                if action == "cancel":
                    connection.execute(
                        "UPDATE reminders SET cancelled_at = ?, updated_at = ? "
                        "WHERE id = ? AND device_id = ?",
                        (now.isoformat(), now.isoformat(), reminder_id, device_id),
                    )
                elif action == "complete":
                    connection.execute(
                        "UPDATE reminders SET completed = 1, completed_at = ?, "
                        "updated_at = ? WHERE id = ? AND device_id = ?",
                        (now.isoformat(), now.isoformat(), reminder_id, device_id),
                    )
                else:
                    connection.execute(
                        "UPDATE reminders SET due_at = ?, delivered_at = NULL, "
                        "delivery_attempted_at = NULL, delivery_attempts = 0, "
                        "updated_at = ? WHERE id = ? AND device_id = ?",
                        (due_at.isoformat(), now.isoformat(), reminder_id, device_id),
                    )
                updated = connection.execute(
                    "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
                ).fetchone()
                result = {
                    "ok": True,
                    "action": action,
                    "reminder": self._from_row(updated).model_dump(mode="json"),
                }

            connection.execute(
                "INSERT INTO reminder_actions "
                "(idempotency_key, reminder_id, device_id, action, result_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    idempotency_key,
                    reminder_id,
                    device_id,
                    action,
                    json.dumps(result),
                    now.isoformat(),
                ),
            )
            connection.commit()
        return result

    def conversation(self, device_id: str, limit: int = 10, *, exclude_calendar: bool = False) -> list[dict[str, Any]]:
        excluded = ("AND request_id NOT IN (SELECT request_id FROM conversation_messages "
                    "WHERE device_id = ? AND name IN ('get_calendar', 'google_calendar')) ") if exclude_calendar else ""
        parameters = (device_id, device_id, device_id, limit) if exclude_calendar else (device_id, device_id, limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_messages WHERE device_id = ? "
                "AND request_id IN (SELECT request_id FROM conversation_messages "
                "WHERE device_id = ? " + excluded +
                "GROUP BY request_id ORDER BY MAX(id) DESC LIMIT ?) ORDER BY id",
                parameters,
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            message: dict[str, Any] = {
                "role": row["role"],
                "content": row["content"],
            }
            if row["name"]:
                message["name"] = row["name"]
            if row["tool_call_id"]:
                message["tool_call_id"] = row["tool_call_id"]
            if row["tool_calls_json"]:
                try:
                    tool_calls = json.loads(row["tool_calls_json"])
                except (json.JSONDecodeError, TypeError):
                    tool_calls = []
                if isinstance(tool_calls, list) and tool_calls:
                    message["tool_calls"] = tool_calls
            messages.append(message)
        return messages

    def append_messages(
        self,
        device_id: str,
        request_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        rows = []
        for index, message in enumerate(messages):
            rows.append(
                (
                    f"{request_id}:{index}",
                    device_id,
                    request_id,
                    str(message.get("role", "")),
                    str(message.get("content", "")),
                    message.get("name"),
                    message.get("tool_call_id"),
                    json.dumps(message["tool_calls"], sort_keys=True)
                    if message.get("tool_calls")
                    else None,
                    now,
                )
            )
        with closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    "INSERT OR IGNORE INTO conversation_messages "
                    "(event_key, device_id, request_id, role, content, name, "
                    "tool_call_id, tool_calls_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )

    def append_exchange(
        self, device_id: str, request_id: str, user_text: str, assistant_text: str
    ) -> None:
        self.append_messages(
            device_id,
            request_id,
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ],
        )

    def request_confirmation(
        self,
        confirmation_id: str,
        device_id: str,
        request_id: str,
        actions: list[dict[str, Any]],
        summary: str,
        expires_at: datetime,
    ) -> PendingConfirmation:
        check_time = datetime.now(timezone.utc)
        now = check_time.isoformat()
        reclaim_before = (check_time - timedelta(minutes=2)).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_confirmations SET status = 'expired' "
                    "WHERE device_id = ? AND status IN ('pending', 'executing') "
                    "AND expires_at <= ?",
                    (device_id, now),
                )
                connection.execute(
                    "UPDATE pending_confirmations SET status = 'pending', claimed_at = NULL "
                    "WHERE device_id = ? AND status = 'executing' "
                    "AND claimed_at <= ? AND expires_at > ?",
                    (device_id, reclaim_before, now),
                )
                existing = connection.execute(
                    "SELECT * FROM pending_confirmations WHERE device_id = ? "
                    "AND status = 'pending' ORDER BY created_at LIMIT 1",
                    (device_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT OR IGNORE INTO pending_confirmations "
                        "(id, device_id, request_id, actions_json, summary, status, "
                        "created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                        (
                            confirmation_id,
                            device_id,
                            request_id,
                            json.dumps(actions, sort_keys=True),
                            summary,
                            now,
                            expires_at.astimezone(timezone.utc).isoformat(),
                        ),
                    )
                row = connection.execute(
                    "SELECT * FROM pending_confirmations WHERE device_id = ? "
                    "AND status = 'pending' ORDER BY created_at LIMIT 1",
                    (device_id,),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT * FROM pending_confirmations WHERE id = ?",
                        (confirmation_id,),
                    ).fetchone()
        return self._confirmation(row)

    def pending_confirmation(
        self, device_id: str, now: datetime | None = None
    ) -> PendingConfirmation | None:
        check_time = now or datetime.now(timezone.utc)
        if check_time.tzinfo is None:
            check_time = check_time.replace(tzinfo=timezone.utc)
        check_time = check_time.astimezone(timezone.utc)
        timestamp = check_time.isoformat()
        reclaim_before = (check_time - timedelta(minutes=2)).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_confirmations SET status = 'expired' "
                    "WHERE device_id = ? AND status IN ('pending', 'executing') "
                    "AND expires_at <= ?",
                    (device_id, timestamp),
                )
                connection.execute(
                    "UPDATE pending_confirmations SET status = 'pending', claimed_at = NULL "
                    "WHERE device_id = ? AND status = 'executing' "
                    "AND claimed_at <= ? AND expires_at > ?",
                    (device_id, reclaim_before, timestamp),
                )
                row = connection.execute(
                    "SELECT * FROM pending_confirmations WHERE device_id = ? "
                    "AND status = 'pending' ORDER BY created_at LIMIT 1",
                    (device_id,),
                ).fetchone()
        return self._confirmation(row) if row is not None else None

    @staticmethod
    def _confirmation(row: sqlite3.Row) -> PendingConfirmation:
        return PendingConfirmation(
            id=row["id"],
            device_id=row["device_id"],
            request_id=row["request_id"],
            actions=json.loads(row["actions_json"]),
            summary=row["summary"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
            status=row["status"],
        )

    def cancel_pending_confirmation(self, confirmation_id: str, device_id: str) -> bool:
        """Cancel only this owner's current, unexpired, unclaimed request."""
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE pending_confirmations SET status = 'cancelled' "
                    "WHERE id = ? AND device_id = ? AND status = 'pending' AND expires_at > ? "
                    "AND id = (SELECT id FROM pending_confirmations WHERE device_id = ? "
                    "AND status = 'pending' AND expires_at > ? ORDER BY created_at LIMIT 1)",
                    (confirmation_id, device_id, now, device_id, now),
                )
        return cursor.rowcount == 1

    def claim_confirmation(self, confirmation_id: str, device_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE pending_confirmations SET status = 'executing', claimed_at = ? "
                    "WHERE id = ? AND device_id = ? AND status = 'pending' "
                    "AND expires_at > ?",
                    (now, confirmation_id, device_id, now),
                )
        return cursor.rowcount == 1

    def finish_confirmation(self, confirmation_id: str, status: str) -> None:
        if status not in {"confirmed", "cancelled", "failed"}:
            raise ValueError("Invalid confirmation status")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE pending_confirmations SET status = ? WHERE id = ?",
                    (status, confirmation_id),
                )
