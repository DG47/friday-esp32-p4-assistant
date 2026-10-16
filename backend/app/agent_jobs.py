from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agent import FridayAgent
from .device_hub import DeviceHub
from .models import AgentReply
from .execution_authority import execution_scope
from .providers.local_policy import LocalModelUnavailable, LOCAL_FAILURE_MESSAGES


logger = logging.getLogger(__name__)
UNCERTAIN_CLAIM_ERROR = "interrupted_execution_outcome_uncertain"


@dataclass
class AgentJob:
    id: str
    device_id: str
    text: str
    status: str
    attempts: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reply_json: str | None = None
    error: str | None = None
    delivery_id: str = ""
    delivery_attempted_at: str | None = None


class JobAdmissionError(ValueError):
    code = "request_conflict"


class CompletedRequestReplay(JobAdmissionError):
    code = "request_already_finished"

    def __init__(self):
        super().__init__("That request already finished. No action was repeated. Start a new request to continue.")


class UncertainExecutionPending(JobAdmissionError):
    code = "execution_outcome_uncertain"

    def __init__(self):
        super().__init__("A previous action has an unconfirmed outcome. No new work was started. Review the outcome on the Mac before retrying; it will not be replayed automatically.")


class AgentJobStore:
    def __init__(self, path: Path):
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def uncertain_for(self, device_id):
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM agent_jobs WHERE device_id=? AND status='running' AND error=? LIMIT 1",
                (device_id, UNCERTAIN_CLAIM_ERROR),
            ).fetchone() is not None

    def claim_current(self, job):
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM agent_jobs WHERE id=? AND device_id=? AND result_delivery_id=? AND attempts=? AND status='running' AND (error IS NULL OR error!=?)",
                (job.id,job.device_id,job.delivery_id,job.attempts,UNCERTAIN_CLAIM_ERROR),
            ).fetchone() is not None

    def request_execution_stop(self, device, job_id, incarnation, stop_id):
        timestamp=datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute('SELECT * FROM agent_jobs WHERE id=? AND device_id=? AND result_delivery_id=?',(job_id,device,incarnation)).fetchone()
            if row is None:raise ValueError('Execution target is unavailable or changed')
            prior=connection.execute('SELECT response FROM execution_stops WHERE device=? AND job=? AND incarnation=?',(device,job_id,incarnation)).fetchone()
            if prior:return json.loads(prior[0])
            never_admitted=row['status']=='queued' and row['attempts']==0
            revoked=row['status'] in {'queued','running'}
            if never_admitted:
                connection.execute("UPDATE agent_jobs SET status='failed',error='user_stop_before_admission',lease_expires_at=NULL,updated_at=? WHERE id=?",(timestamp,job_id))
                state='stopped_before_admission'
            elif revoked:
                connection.execute("UPDATE agent_jobs SET status='running',error=?,updated_at=? WHERE id=?",(UNCERTAIN_CLAIM_ERROR,timestamp,job_id))
                state='outcome_uncertain'
            elif row['status'] in {'completed','failed','expired','late'}:state='already_terminal'
            else:raise ValueError('Execution state is unavailable')
            result={'id':job_id,'execution_id':incarnation,'state':state,'generation':int(revoked),'no_execution_admitted':never_admitted}
            connection.execute('INSERT INTO execution_stops VALUES(?,?,?,?,?,?)',(device,job_id,incarnation,stop_id,json.dumps(result),timestamp))
            return result

    def execution_recovery(self, device):
        with closing(self._connect()) as connection:
            row=connection.execute("SELECT id,result_delivery_id FROM agent_jobs WHERE device_id=? AND status='running' AND error=? ORDER BY created_at,id LIMIT 1",(device,UNCERTAIN_CLAIM_ERROR)).fetchone()
            return {'job_id':row['id'],'execution_id':row['result_delivery_id']} if row else None

    def execution_status(self, device, job_id, incarnation):
        with closing(self._connect()) as connection:
            row=connection.execute('SELECT status,error FROM agent_jobs WHERE id=? AND device_id=? AND result_delivery_id=?',(job_id,device,incarnation)).fetchone()
            if row is None:raise ValueError('Execution target is unavailable or changed')
            stop=connection.execute('SELECT response FROM execution_stops WHERE device=? AND job=? AND incarnation=?',(device,job_id,incarnation)).fetchone()
            return {'job_status':row['status'],'authority_revoked':bool(stop and json.loads(stop[0])['generation']),
                    'outcome_uncertain':row['status']=='running' and row['error']==UNCERTAIN_CLAIM_ERROR,
                    'no_execution_admitted':row['error']=='user_stop_before_admission'}

    def active_for(self, device_id):
        with closing(self._connect()) as connection:
            return connection.execute("SELECT 1 FROM agent_jobs WHERE device_id=? AND status IN ('queued','running') LIMIT 1", (device_id,)).fetchone() is not None

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
                if "result_ack_required" not in columns:
                    connection.execute("ALTER TABLE agent_jobs ADD COLUMN result_ack_required INTEGER NOT NULL DEFAULT 0")
                if "result_delivery_id" not in columns:
                    connection.execute("ALTER TABLE agent_jobs ADD COLUMN result_delivery_id TEXT")
                connection.execute("UPDATE agent_jobs SET result_delivery_id = lower(hex(randomblob(16))) WHERE result_delivery_id IS NULL")
                if "lease_expires_at" not in columns:
                    connection.execute(
                        "ALTER TABLE agent_jobs ADD COLUMN lease_expires_at TEXT"
                    )
                if "result_attempted_at" not in columns:
                    connection.execute(
                        "ALTER TABLE agent_jobs ADD COLUMN result_attempted_at TEXT"
                    )
                connection.execute("CREATE TABLE IF NOT EXISTS execution_stops(device TEXT NOT NULL,job TEXT NOT NULL,incarnation TEXT NOT NULL,stop_id TEXT NOT NULL,response TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(device,job,incarnation))")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS agent_jobs_claimable "
                    "ON agent_jobs(status, available_at, created_at, device_id)"
                )

    @staticmethod
    def _job(row: sqlite3.Row) -> AgentJob:
        return AgentJob(
            id=row["id"],
            device_id=row["device_id"],
            text=row["text"],
            status=row["status"],
            attempts=row["attempts"],
            created_at=datetime.fromisoformat(row["created_at"]),
            reply_json=row["reply_json"],
            error=row["error"],
            delivery_id=row["result_delivery_id"],
            delivery_attempted_at=row["result_attempted_at"],
        )

    def enqueue(
        self, device_id: str, text: str, request_id: str | None = None
    ) -> AgentJob:
        job_id = request_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                existing = connection.execute(
                    "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if connection.execute(
                    "SELECT 1 FROM agent_jobs WHERE device_id=? AND status='running' AND error=? LIMIT 1",
                    (device_id, UNCERTAIN_CLAIM_ERROR),
                ).fetchone():
                    raise UncertainExecutionPending()
                if existing is None:
                    pending = connection.execute(
                        "SELECT COUNT(*) FROM agent_jobs WHERE device_id = ? "
                        "AND status IN ('queued', 'running')", (device_id,)
                    ).fetchone()[0]
                    if pending >= 32:
                        raise ValueError("Too many pending requests; try again later")
                connection.execute(
                    "INSERT OR IGNORE INTO agent_jobs "
                    "(id, device_id, text, status, available_at, created_at, updated_at, result_delivery_id) "
                    "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)",
                    (job_id, device_id, text, now, now, now, uuid.uuid4().hex),
                )
                row = connection.execute(
                    "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
                ).fetchone()
        if row["device_id"] != device_id or row["text"] != text:
            raise ValueError("request_id is already assigned to another request")
        return self._job(row)

    def recover_interrupted(self, now: datetime | None = None, *, exclude_job_ids=()) -> int:
        timestamp = (now or datetime.now(timezone.utc)).isoformat()
        owned = tuple(exclude_job_ids)
        exclusion = " AND id NOT IN (" + ",".join("?" for _ in owned) + ")" if owned else ""
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE agent_jobs SET status = 'queued', available_at = ?, "
                    "lease_expires_at = NULL, updated_at = ? WHERE status = 'running' "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= ?) "
                    "AND (error IS NULL OR error != ?)" + exclusion,
                    (timestamp, timestamp, timestamp, UNCERTAIN_CLAIM_ERROR, *owned),
                )
        return cursor.rowcount

    def mark_execution_uncertain(self, job: AgentJob) -> bool:
        # Keep the running claim: cancellation does not prove an external action
        # stopped. Existing columns suffice; never automatically replay this row.
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE agent_jobs SET error = ? WHERE id = ? AND status = 'running' AND attempts = ? AND result_delivery_id = ?",
                (UNCERTAIN_CLAIM_ERROR, job.id, job.attempts, job.delivery_id),
            )
        return cursor.rowcount == 1

    def get(self, job_id: str) -> AgentJob | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job(row) if row is not None else None

    def claim_next(
        self,
        now: datetime | None = None,
        lease_seconds: float = 180.0,
        max_age_seconds: float = 300.0,
    ) -> AgentJob | None:
        claimed_at = now or datetime.now(timezone.utc)
        timestamp = claimed_at.isoformat()
        oldest_allowed = (
            claimed_at - timedelta(seconds=max_age_seconds)
        ).isoformat()
        lease_expires_at = (claimed_at + timedelta(seconds=lease_seconds)).isoformat()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT candidate.* FROM agent_jobs AS candidate "
                "WHERE candidate.status = 'queued' "
                "AND candidate.available_at <= ? AND candidate.created_at > ? "
                "AND NOT EXISTS (SELECT 1 FROM agent_jobs AS active "
                "WHERE active.device_id = candidate.device_id "
                "AND active.status = 'running') "
                "AND NOT EXISTS (SELECT 1 FROM agent_jobs AS earlier "
                "WHERE earlier.device_id = candidate.device_id "
                "AND earlier.status IN ('queued', 'running') "
                "AND (earlier.created_at < candidate.created_at "
                "OR (earlier.created_at = candidate.created_at "
                "AND earlier.rowid < candidate.rowid))) "
                "ORDER BY candidate.created_at, candidate.rowid LIMIT 1",
                (timestamp, oldest_allowed),
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

    def complete(
        self, job_id: str, reply: AgentReply, max_age_seconds: float = 300.0,
        *, claim: AgentJob | None = None,
    ) -> str | None:
        # Claimed asynchronous workers must supply their attempt and incarnation.
        # By-ID completion remains a trusted fixture/administrative compatibility path.
        if claim is not None and claim.id != job_id:
            raise ValueError("Completion claim identity mismatch")
        guard = (
            " AND status = 'running' AND attempts = ? AND result_delivery_id = ?"
            " AND (error IS NULL OR error != ?)"
        ) if claim is not None else ""
        guard_values = (
            (claim.attempts, claim.delivery_id, UNCERTAIN_CLAIM_ERROR)
            if claim is not None else ()
        )
        completed_at = datetime.now(timezone.utc)
        now = completed_at.isoformat()
        with closing(self._connect()) as connection:
            with connection:
                row = connection.execute(
                    "SELECT created_at FROM agent_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    if claim is not None:
                        return None
                    raise ValueError(f"Unknown agent job: {job_id}")
                created_at = datetime.fromisoformat(row["created_at"])
                status = (
                    "late"
                    if (completed_at - created_at).total_seconds() > max_age_seconds
                    else "completed"
                )
                cursor = connection.execute(
                    "UPDATE agent_jobs SET status = ?, reply_json = ?, "
                    "error = ?, lease_expires_at = NULL, updated_at = ? WHERE id = ?" + guard,
                    (
                        status,
                        reply.model_dump_json(),
                        "Result completed after its maximum useful age"
                        if status == "late"
                        else None,
                        now,
                        job_id,
                        *guard_values,
                    ),
                )
        return status if cursor.rowcount == 1 else None

    def expire_stale(
        self, now: datetime | None = None, max_age_seconds: float = 300.0
    ) -> int:
        check_time = now or datetime.now(timezone.utc)
        timestamp = check_time.isoformat()
        cutoff = (check_time - timedelta(seconds=max_age_seconds)).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                queued = connection.execute(
                    "UPDATE agent_jobs SET status = 'expired', "
                    "error = 'Request expired before execution', updated_at = ? "
                    "WHERE status = 'queued' AND created_at <= ?",
                    (timestamp, cutoff),
                ).rowcount
                completed = connection.execute(
                    "UPDATE agent_jobs SET status = 'late', "
                    "error = 'Result became stale before delivery', updated_at = ? "
                    "WHERE status = 'completed' AND result_delivered_at IS NULL "
                    "AND created_at <= ?",
                    (timestamp, cutoff),
                ).rowcount
        return queued + completed

    def fail(self, job: AgentJob, error: str, max_attempts: int = 3) -> str | None:
        now = datetime.now(timezone.utc)
        final = job.attempts >= max_attempts
        status = "failed" if final else "queued"
        available = now + timedelta(seconds=min(30, 2 ** max(0, job.attempts - 1)))
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE agent_jobs SET status = ?, error = ?, available_at = ?, "
                    "lease_expires_at = NULL, updated_at = ? WHERE id = ? AND status = 'running' "
                    "AND attempts = ? AND result_delivery_id = ? AND (error IS NULL OR error != ?)",
                    (
                        status,
                        error[:1000],
                        available.isoformat(),
                        now.isoformat(),
                        job.id,
                        job.attempts,
                        job.delivery_id,
                        UNCERTAIN_CLAIM_ERROR,
                    ),
                )
        return status if cursor.rowcount == 1 else None

    def pending_results(self, device_id: str) -> list[AgentJob]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM agent_jobs WHERE device_id = ? "
                "AND status IN ('completed', 'failed', 'expired', 'late') "
                "AND result_delivered_at IS NULL "
                "ORDER BY created_at LIMIT 64",
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
                    "WHERE id = ? AND status IN "
                    "('completed', 'failed', 'expired', 'late') "
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
                    "WHERE id = ? AND result_delivered_at IS NULL AND result_ack_required = 0",
                    (now, now, job_id),
                )

    def require_result_ack(self, job_id: str) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("UPDATE agent_jobs SET result_ack_required = 1 WHERE id = ?", (job_id,))

    def acknowledge_result(self, device_id: str, job_id: str, delivery_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE agent_jobs SET result_delivered_at = COALESCE(result_delivered_at, ?), "
                    "updated_at = CASE WHEN result_delivered_at IS NULL THEN ? ELSE updated_at END "
                    "WHERE id = ? AND device_id = ? AND result_delivery_id = ? "
                    "AND status IN ('completed', 'failed', 'expired', 'late') AND result_attempted_at IS NOT NULL",
                    (now, now, job_id, device_id, delivery_id),
                )
        return cursor.rowcount == 1

    def prune_results(self, now: datetime | None = None) -> int:
        """Bound terminal delivery records; leave active jobs/conversation/tool state intact."""
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=7)).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("DELETE FROM execution_stops WHERE NOT EXISTS (SELECT 1 FROM agent_jobs WHERE agent_jobs.id=execution_stops.job AND agent_jobs.device_id=execution_stops.device AND agent_jobs.result_delivery_id=execution_stops.incarnation)")
                cursor = connection.execute(
                    "DELETE FROM agent_jobs WHERE status IN ('completed', 'failed', 'expired', 'late') "
                    "AND (created_at < ? OR id IN (SELECT id FROM agent_jobs "
                    "WHERE status IN ('completed', 'failed', 'expired', 'late') "
                    "ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET 10000))", (cutoff,),
                )
        return cursor.rowcount


class AgentJobWorker:
    def __init__(
        self,
        store: AgentJobStore,
        agent: FridayAgent,
        hub: DeviceHub,
        timeout_seconds: float = 120.0,
        max_age_seconds: float = 300.0,
        concurrency: int = 4,
    ) -> None:
        self.on_reply = None
        self.on_finished = None
        self.store = store
        self.agent = agent
        self.hub = hub
        self.timeout_seconds = timeout_seconds
        self.max_age_seconds = max(10.0, max_age_seconds)
        self.concurrency = max(1, min(16, concurrency))
        self._claimed_jobs = set()
        self._handlers = {}
        self._abandoned_jobs = set()

    async def _publish(self, job: AgentJob, reply: AgentReply) -> int:
        with self.hub.experience.for_job(job.id):
            return await self._publish_owned(job, reply)

    async def _publish_owned(self, job: AgentJob, reply: AgentReply) -> int:
        if reply.surface.countdown_ms and reply.surface.deadline_at:
            remaining = int((reply.surface.deadline_at - datetime.now(timezone.utc)).total_seconds() * 1000)
            if remaining < 1000:
                reply.surface.countdown_ms = None
                reply.surface.kind = "message"
                reply.surface.title = "Timer finished"
                reply.surface.subtitle = "The timer finished. Reminder acknowledgement is separate."
            else:
                reply.surface.countdown_ms = min(86400000, remaining)
        if reply.tool and job.delivery_attempted_at is None:
            await self.hub.broadcast(
                job.device_id,
                {"type": "tool.activity", "name": reply.tool, "status": "complete"},
            )
        acknowledged_peers = await self.hub.broadcast(job.device_id, {
            "type": "agent.result", "job_id": job.id, "delivery_id": job.delivery_id,
            "status": "completed", "text": reply.text, "provider": reply.provider,
            **({"retain_context":True} if reply.retain_context else {}),
            "surface": reply.surface.model_dump(mode="json")}, required_capability="result_ack")
        await self.hub.broadcast(
            job.device_id,
            {"type": "surface", "surface": reply.surface.model_dump(mode="json"),**({"retain_context":True} if reply.retain_context else {})},
            excluded_capability="result_ack",
        )
        recipients = await self.hub.broadcast(
            job.device_id,
            {
                "type": "assistant.text",
                "text": reply.text,
                "provider": reply.provider,
                "job_id": job.id,
            },
            excluded_capability="result_ack",
        )
        owned = not self.hub.has_capability(job.device_id, "experience_context_v1") or self.hub.experience.current.get(job.device_id) == self.hub.experience.origin.get()
        if self.on_reply and job.delivery_attempted_at is None and owned:
            await self.on_reply(job.device_id, reply.text, job.id)
        if job.delivery_attempted_at is None:
            await self.hub.broadcast(job.device_id, {"type": "state", "state": "idle"}, excluded_capability="pcm_speech")
        await self.hub.broadcast(job.device_id, {"type":"experience.finished"}, required_capability="experience_context_v1")
        return recipients + acknowledged_peers

    async def deliver_pending(self, device_id: str) -> int:
        delivered = 0
        for job in self.store.pending_results(device_id):
            with self.hub.experience.for_job(job.id):
                reason = (job.error or '').removeprefix('local-only:') if (job.error or '').startswith('local-only:') else ''
                local_failure = LOCAL_FAILURE_MESSAGES.get(reason)
                stopped_before = job.error == 'user_stop_before_admission'
                stop_text = 'Stopped before execution admission. No operation from this request was started.' if stopped_before else None
                if not self.store.claim_result_delivery(
                    job.id, datetime.now(timezone.utc)
                ):
                    continue
                if self.hub.has_capability(device_id, "result_ack"):
                    self.store.require_result_ack(job.id)
                acknowledged_peers = 0
                if job.status in {"expired", "late", "failed"}:
                    text = (stop_text or local_failure or "Friday could not complete that request.") if job.status == "failed" else "That request is no longer current, so I discarded its result."
                    acknowledged_peers = await self.hub.broadcast(job.device_id, {"type": "agent.result", "job_id": job.id, "delivery_id": job.delivery_id, "status": job.status, "text": text, "surface": {"kind": "message", "title": "Stopped before admission" if stopped_before else "Local reasoning unavailable" if local_failure else "Request unavailable", "subtitle": text}}, required_capability="result_ack")
                if job.status in {"expired", "late"}:
                    recipients = await self.hub.broadcast(
                        job.device_id,
                        {
                            "type": "error",
                            "code": "agent_stale",
                            "message": "That request is no longer current, so I discarded its result.",
                            "job_id": job.id,
                        },
                        excluded_capability="result_ack",
                    )
                    if job.delivery_attempted_at is None:
                        await self.hub.broadcast(
                            job.device_id, {"type": "state", "state": "idle"}
                        )
                elif job.status == "failed":
                    recipients = await self.hub.broadcast(
                        job.device_id,
                        {
                            "type": "error",
                            "code": "local_reasoning_unavailable" if local_failure else "agent_failed",
                            "message": stop_text or local_failure or "Friday could not complete that request.",
                            "job_id": job.id,
                        },
                        excluded_capability="result_ack",
                    )
                    if job.delivery_attempted_at is None:
                        await self.hub.broadcast(
                            job.device_id, {"type": "state", "state": "idle"}
                        )
                elif job.reply_json:
                    reply = AgentReply.model_validate(json.loads(job.reply_json))
                    recipients = await self._publish(job, reply)
                else:
                    recipients = 0
                if recipients + acknowledged_peers > 0:
                    # Mixed clients must still wait for an acknowledgement-capable UI.
                    if not self.hub.has_capability(device_id, "result_ack"):
                        self.store.mark_result_delivered(job.id)
                    delivered += 1
        return delivered

    async def _handle_owned(self, job):
        with execution_scope(lambda:self.store.claim_current(job)), self.hub.experience.for_job(job.id):
            return await self.agent.handle(job.device_id, job.text, request_id=job.id)

    async def process_once(self) -> bool:
        self.store.expire_stale(max_age_seconds=self.max_age_seconds)
        job = self.store.claim_next(
            lease_seconds=max(180.0, self.timeout_seconds + 30.0),
            max_age_seconds=self.max_age_seconds,
        )
        if job is None:
            return False
        # No yield between claiming and registering ownership. Track both the
        # slot and its child: repeated slot cancellation may outlive wait_for.
        self._claimed_jobs.add(job.id)
        try:
            return await self._process_claimed(job)
        finally:
            self._claimed_jobs.discard(job.id)

    def recover_interrupted(self, now=None):
        owned = self._claimed_jobs | self._handlers.keys() | self._abandoned_jobs
        return self.store.recover_interrupted(now, exclude_job_ids=tuple(owned))

    def _handler_finished(self, job_id, task):
        if not task.cancelled():
            task.exception()  # Consume abandoned failures without logging private payloads.
        if self._handlers.get(job_id) is task:
            self._handlers.pop(job_id)

    def _retain_uncertain_claim(self, job):
        self._abandoned_jobs.add(job.id)
        try:
            current = self.store.mark_execution_uncertain(job)
            self._abandoned_jobs.discard(job.id)  # The durable recovery filter now owns this fence.
        except Exception:
            logger.error("Uncertain claim persistence failed; local automatic recovery remains paused")
            return False
        if current:
            logger.warning("Agent execution interrupted; outcome unconfirmed, automatic claim replay blocked")
        return current

    async def _process_claimed(self, job):
        handler = asyncio.create_task(self._handle_owned(job))
        self._handlers[job.id] = handler
        handler.add_done_callback(lambda task: self._handler_finished(job.id, task))
        try:
            reply = await asyncio.wait_for(
                handler,
                timeout=self.timeout_seconds,
            )
        except asyncio.CancelledError:
            self._retain_uncertain_claim(job)
            raise
        except TimeoutError:
            # A cancelled coroutine can leave shielded/native/remote work alive.
            # Do not turn a timeout into an automatic repeat of an unknown action.
            if not self._retain_uncertain_claim(job):
                return True
            with self.hub.experience.for_job(job.id):
                await self.hub.broadcast(job.device_id, {
                    "type": "error", "code": "execution_outcome_uncertain",
                    "message": "The request timed out without a confirmed outcome. Check before retrying.",
                })
                await self.hub.broadcast(job.device_id, {"type": "state", "state": "idle"})
                await self.hub.broadcast(job.device_id, {"type": "experience.finished"}, required_capability="experience_context_v1")
            if self.on_finished:
                await self.on_finished(job.device_id)
            return True
        except LocalModelUnavailable as error:
            # Do not retry a known unavailable local model or obscure it behind a
            # generic failure. Persist only a safe code, never provider payloads.
            reason = error.reason if error.reason in LOCAL_FAILURE_MESSAGES else 'local_inference_failed'
            if self.store.fail(job, 'local-only:' + reason, max_attempts=1) is None:
                return True
            logger.warning('Local reasoning unavailable: %s', reason)
            if job.device_id in self.hub.device_ids():
                await self.deliver_pending(job.device_id)
            if self.on_finished:
                await self.on_finished(job.device_id)
            return True
        except Exception as error:
            status = self.store.fail(job, str(error))
            if status is None:
                return True
            if status == "failed":
                logger.exception("Agent job %s failed permanently", job.id)
                if job.device_id in self.hub.device_ids():
                    await self.deliver_pending(job.device_id)
            else:
                logger.warning(
                    "Agent job %s failed and will retry: %s", job.id, error
                )
            if self.on_finished:
                await self.on_finished(job.device_id)
            return True

        if self.store.complete(
            job.id, reply, max_age_seconds=self.max_age_seconds, claim=job,
        ) is None:
            return True
        if job.device_id in self.hub.device_ids():
            await self.deliver_pending(job.device_id)
        if self.on_finished:
            await self.on_finished(job.device_id)
        return True

    async def _run_slot(self) -> None:
        while True:
            try:
                worked = await self.process_once()
            except Exception:
                logger.exception("Agent worker iteration failed; will retry")
                await asyncio.sleep(1.0)
                continue
            if not worked:
                await asyncio.sleep(0.1)

    async def _run_maintenance(self) -> None:
        while True:
            try:
                self.recover_interrupted()
                self.store.prune_results()
                self.store.expire_stale(max_age_seconds=self.max_age_seconds)
                for device_id in self.hub.device_ids():
                    await self.deliver_pending(device_id)
            except Exception:
                logger.exception("Agent maintenance iteration failed; will retry")
            await asyncio.sleep(0.5)

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._run_slot(), name=f"friday-agent-slot-{index}")
            for index in range(self.concurrency)
        ]
        tasks.append(
            asyncio.create_task(
                self._run_maintenance(), name="friday-agent-maintenance"
            )
        )
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
