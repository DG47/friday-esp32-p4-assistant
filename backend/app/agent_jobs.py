from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agent import FridayAgent
from .device_hub import DeviceHub
from .models import AgentReply


logger = logging.getLogger(__name__)


@dataclass
class AgentJob:
    id: str
    device_id: str
    text: str
    status: str
    attempts: int
    reply_json: str | None = None
    error: str | None = None


class AgentJobStore:
    def __init__(self, path: Path):
        self.path = path
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
                    CREATE TABLE IF NOT EXISTS agent_jobs (
                        id TEXT PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        text TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        available_at TEXT NOT NULL,
                        reply_json TEXT,
                        error TEXT,
                        lease_expires_at TEXT,
                        result_attempted_at TEXT,
                        result_delivered_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(agent_jobs)").fetchall()
                }
                if "lease_expires_at" not in columns:
                    connection.execute(
                        "ALTER TABLE agent_jobs ADD COLUMN lease_expires_at TEXT"
                    )
                if "result_attempted_at" not in columns:
                    connection.execute(
                        "ALTER TABLE agent_jobs ADD COLUMN result_attempted_at TEXT"
                    )

    @staticmethod
    def _job(row: sqlite3.Row) -> AgentJob:
        return AgentJob(
            id=row["id"],
            device_id=row["device_id"],
            text=row["text"],
            status=row["status"],
            attempts=row["attempts"],
            reply_json=row["reply_json"],
            error=row["error"],
        )

    def enqueue(
        self, device_id: str, text: str, request_id: str | None = None
    ) -> AgentJob:
        job_id = request_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO agent_jobs "
                    "(id, device_id, text, status, available_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                    (job_id, device_id, text, now, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
                ).fetchone()
        if row["device_id"] != device_id or row["text"] != text:
            raise ValueError("request_id is already assigned to another request")
        return self._job(row)

    def recover_interrupted(self, now: datetime | None = None) -> int:
        timestamp = (now or datetime.now(timezone.utc)).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE agent_jobs SET status = 'queued', available_at = ?, "
                    "lease_expires_at = NULL, updated_at = ? WHERE status = 'running' "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)",
                    (timestamp, timestamp, timestamp),
                )
        return cursor.rowcount

    def get(self, job_id: str) -> AgentJob | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job(row) if row is not None else None

    def claim_next(
        self, now: datetime | None = None, lease_seconds: float = 180.0
    ) -> AgentJob | None:
        claimed_at = now or datetime.now(timezone.utc)
        timestamp = claimed_at.isoformat()
        lease_expires_at = (claimed_at + timedelta(seconds=lease_seconds)).isoformat()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE status = 'queued' "
                "AND available_at <= ? ORDER BY created_at LIMIT 1",
                (timestamp,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            cursor = connection.execute(
                "UPDATE agent_jobs SET status = 'running', attempts = attempts + 1, "
                "lease_expires_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (lease_expires_at, timestamp, row["id"]),
            )
            connection.commit()
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._job(row)

    def complete(self, job_id: str, reply: AgentReply) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE agent_jobs SET status = 'completed', reply_json = ?, "
                    "error = NULL, lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                    (reply.model_dump_json(), now, job_id),
                )

    def fail(self, job: AgentJob, error: str, max_attempts: int = 3) -> str:
        now = datetime.now(timezone.utc)
        final = job.attempts >= max_attempts
        status = "failed" if final else "queued"
        available = now + timedelta(seconds=min(30, 2 ** max(0, job.attempts - 1)))
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE agent_jobs SET status = ?, error = ?, available_at = ?, "
                    "lease_expires_at = NULL, updated_at = ? WHERE id = ?",
                    (
                        status,
                        error[:1000],
                        available.isoformat(),
                        now.isoformat(),
                        job.id,
                    ),
                )
        return status

    def pending_results(self, device_id: str) -> list[AgentJob]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM agent_jobs WHERE device_id = ? "
                "AND status IN ('completed', 'failed') "
                "AND result_delivered_at IS NULL "
                "ORDER BY created_at",
                (device_id,),
            ).fetchall()
        return [self._job(row) for row in rows]

    def claim_result_delivery(
        self, job_id: str, attempted_at: datetime, retry_seconds: float = 10.0
    ) -> bool:
        retry_before = (attempted_at - timedelta(seconds=retry_seconds)).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE agent_jobs SET result_attempted_at = ?, updated_at = ? "
                    "WHERE id = ? AND status IN ('completed', 'failed') "
                    "AND result_delivered_at IS NULL AND "
                    "(result_attempted_at IS NULL OR result_attempted_at <= ?)",
                    (
                        attempted_at.isoformat(),
                        attempted_at.isoformat(),
                        job_id,
                        retry_before,
                    ),
                )
        return cursor.rowcount == 1

    def mark_result_delivered(self, job_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE agent_jobs SET result_delivered_at = ?, updated_at = ? "
                    "WHERE id = ? AND result_delivered_at IS NULL",
                    (now, now, job_id),
                )


class AgentJobWorker:
    def __init__(
        self,
        store: AgentJobStore,
        agent: FridayAgent,
        hub: DeviceHub,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.store = store
        self.agent = agent
        self.hub = hub
        self.timeout_seconds = timeout_seconds

    async def _publish(self, job: AgentJob, reply: AgentReply) -> int:
        if reply.tool:
            await self.hub.broadcast(
                job.device_id,
                {"type": "tool.activity", "name": reply.tool, "status": "complete"},
            )
        await self.hub.broadcast(
            job.device_id,
            {"type": "surface", "surface": reply.surface.model_dump(mode="json")},
        )
        recipients = await self.hub.broadcast(
            job.device_id,
            {
                "type": "assistant.text",
                "text": reply.text,
                "provider": reply.provider,
                "job_id": job.id,
            },
        )
        await self.hub.broadcast(job.device_id, {"type": "state", "state": "speaking"})
        await asyncio.sleep(min(0.9, max(0.25, len(reply.text) / 180)))
        await self.hub.broadcast(job.device_id, {"type": "state", "state": "idle"})
        return recipients

    async def deliver_pending(self, device_id: str) -> int:
        delivered = 0
        for job in self.store.pending_results(device_id):
            if not self.store.claim_result_delivery(
                job.id, datetime.now(timezone.utc)
            ):
                continue
            if job.status == "failed":
                recipients = await self.hub.broadcast(
                    job.device_id,
                    {
                        "type": "error",
                        "code": "agent_failed",
                        "message": "Friday could not complete that request.",
                        "job_id": job.id,
                    },
                )
                await self.hub.broadcast(
                    job.device_id, {"type": "state", "state": "idle"}
                )
            elif job.reply_json:
                reply = AgentReply.model_validate(json.loads(job.reply_json))
                recipients = await self._publish(job, reply)
            else:
                recipients = 0
            if recipients > 0:
                self.store.mark_result_delivered(job.id)
                delivered += 1
        return delivered

    async def process_once(self) -> bool:
        job = self.store.claim_next(
            lease_seconds=max(180.0, self.timeout_seconds + 30.0)
        )
        if job is None:
            return False
        try:
            reply = await asyncio.wait_for(
                self.agent.handle(job.device_id, job.text, request_id=job.id),
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            status = self.store.fail(job, str(error))
            if status == "failed":
                logger.exception("Agent job %s failed permanently", job.id)
                if job.device_id in self.hub.device_ids():
                    await self.deliver_pending(job.device_id)
            else:
                logger.warning(
                    "Agent job %s failed and will retry: %s", job.id, error
                )
            return True

        self.store.complete(job.id, reply)
        if job.device_id in self.hub.device_ids():
            await self.deliver_pending(job.device_id)
        return True

    async def run(self) -> None:
        while True:
            self.store.recover_interrupted()
            worked = await self.process_once()
            for device_id in self.hub.device_ids():
                await self.deliver_pending(device_id)
            if not worked:
                await asyncio.sleep(0.1)
