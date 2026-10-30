"""Online SQLite backups with retention. Never overwrites a file and never reads private rows."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

BACKUP_NAME = re.compile(r"^friday-(\d{8}T\d{6}Z)\.db$")
STAMP = "%Y%m%dT%H%M%SZ"


def backup_database(source: Path, destination: Path) -> None:
    """Copy a live SQLite database into a new 0600 file and verify its integrity."""
    if not source.is_file():
        raise ValueError("Source database does not exist")
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as backup:
                original.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
                backup.execute("PRAGMA journal_mode=DELETE")
                backup.commit()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def backup_time(path: Path) -> datetime | None:
    match = BACKUP_NAME.match(path.name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), STAMP).replace(tzinfo=timezone.utc)


@dataclass
class BackupStatus:
    enabled: bool = False
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    retained: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class BackupScheduler:
    """Creates a backup when the newest one is older than the interval, then prunes old ones."""

    def __init__(
        self,
        database_path: Path,
        directory: Path,
        interval_seconds: float,
        keep_count: int = 14,
        keep_days: int = 30,
        poll_seconds: float = 60.0,
        on_failure: Callable[[str, str], Awaitable[object]] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.directory = Path(directory)
        self.interval = timedelta(seconds=max(0.0, interval_seconds))
        self.keep_count = max(1, keep_count)
        self.keep_days = max(1, keep_days)
        self.poll_seconds = max(1.0, poll_seconds)
        self.on_failure = on_failure
        self.status = BackupStatus(enabled=self.interval.total_seconds() > 0)
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.status.enabled

    def backups(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        found = [path for path in self.directory.iterdir() if path.is_file() and backup_time(path) is not None]
        return sorted(found, key=lambda path: backup_time(path), reverse=True)

    def latest_time(self) -> datetime | None:
        backups = self.backups()
        return backup_time(backups[0]) if backups else None

    def due(self, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        now = now or datetime.now(timezone.utc)
        latest = self.latest_time()
        return latest is None or now - latest >= self.interval

    def create(self, now: datetime | None = None) -> Path:
        now = now or datetime.now(timezone.utc)
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        destination = self.directory / f"friday-{now.strftime(STAMP)}.db"
        backup_database(self.database_path, destination)
        return destination

    def prune(self, now: datetime | None = None) -> int:
        """Delete backups beyond keep_count or older than keep_days. The newest is always kept."""
        now = now or datetime.now(timezone.utc)
        removed = 0
        for index, path in enumerate(self.backups()):
            if index == 0:
                continue
            stamp = backup_time(path)
            if index >= self.keep_count or (stamp is not None and now - stamp > timedelta(days=self.keep_days)):
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def run_once(self, now: datetime | None = None) -> dict:
        """Synchronous step: back up when due, prune afterwards, record the outcome."""
        now = now or datetime.now(timezone.utc)
        result: dict = {"created": None, "pruned": 0}
        if not self.due(now):
            self.status.retained = len(self.backups())
            return result
        try:
            created = self.create(now)
            result["pruned"] = self.prune(now)
            result["created"] = str(created)
            self.status.last_success_at = now.isoformat()
            self.status.last_error = None
            self.status.consecutive_failures = 0
            logger.info("Database backup written; %s old backups pruned", result["pruned"])
        except Exception as error:
            # Only the exception type is recorded; messages may embed paths.
            self.status.last_failure_at = now.isoformat()
            self.status.last_error = type(error).__name__
            self.status.consecutive_failures += 1
            logger.error("Database backup failed: %s", type(error).__name__)
        self.status.retained = len(self.backups())
        return result

    async def tick(self, now: datetime | None = None) -> dict:
        async with self._lock:
            failures_before = self.status.consecutive_failures
            result = await asyncio.to_thread(self.run_once, now)
            if self.status.consecutive_failures > failures_before and self.on_failure is not None:
                await self.on_failure(
                    "backup_failed",
                    f"Database backup failed ({self.status.last_error}); "
                    f"{self.status.consecutive_failures} consecutive failures",
                )
            return result

    async def run(self) -> None:
        # The first check waits one poll so that short-lived test servers never touch real data.
        while True:
            await asyncio.sleep(self.poll_seconds)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Backup scheduler iteration failed")
