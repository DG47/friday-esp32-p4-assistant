# Scoped execution Stop candidate

Based on b751ece. Source-only candidate: no deployment, flash, account call,
credential/configuration change or integration activation. Report Stop remains
separate. Requires execution_stop_v1 AND result_ack on an authenticated socket.
Legacy packet shapes are preserved. Native support is staged independently.

job.accepted adds execution_id only for negotiated peers. Stop/status name the
same authenticated device, job ID, immutable result-delivery incarnation and
control request ID. No implicit identity mapping or model-inferred target exists.
An audio.start may capture that explicit tuple: an exact recognized Stop then
uses the activation request ID out of band, rather than creating a new agent job.
Recognition time precedes the server observer; no acoustic latency claim is made.

The existing SQLite job DB gains a metadata-only execution_stops table. A single
BEGIN IMMEDIATE transaction verifies scope and records revocation. A queued job
with zero attempts becomes failed/user_stop_before_admission; only that case may
say no operation was admitted. Running jobs and previously attempted queued jobs
stay running/outcome uncertain. Repeated Stop coalesces by job incarnation and
cannot clear uncertainty or replay effects. Existing result/attempt fences remain.

A ContextVar carries the exact worker claim into supported model/tool admission
boundaries, including actual reminder mutations and confirmation handling. Each
boundary rereads durable authority. Operations already admitted across a boundary
can finish; this is not atomic rollback or a universal remote-process kill switch.
Late resistant handlers cannot publish completion, admit another supported tool,
or replace a new speech stream. Speech cancellation retains resistant producers,
observes handler drain for 250ms and fences late frames inside the peer send lock.

The server observer allows 1s for persistence and 250ms for handler observation.
This is not an end-to-end physical deadline: synchronous preexisting socket auth,
event-loop scheduling, transport queues and recognition can add latency. Failed,
delayed or disconnected delivery does not imply success. Outstanding persistence
and resistant handlers retain lifetime ownership; shutdown has no hard deadline.

The browser starts its own monotonic 1s timer at explicit Stop intent. Missing ACK
or disconnect shows Stop not confirmed/work may continue and holds mutations.
Full target/action matching rejects stale ACKs; retries retain the same identity.
Reconnect requests status only. Metadata-only hello recovery reinstates an unknown
hold after a page reload. Handler drained and ordinary result delivery never mean
Stopped. Protected facts and sequential memory confirmation are not rewritten.

Reconciliation GET is loopback/admin-token protected and read-only, and verifies
paired device and exact incarnation. It returns metadata and needs_effect_evidence;
there is no POST, clear or replay. Unknown native/remote effects lack typed durable
outcome evidence in this architecture. Safe writable resolution therefore needs
adapter-specific intent/outcome evidence, or an explicitly reviewed owner
attestation policy. Coroutine drain or model prose cannot establish that proof.
No-tool factual accuracy and cross-identity sharing remain unproved/unimplemented.

Validation: 667 provider-free backend tests pass (21.108s), including resistant
actual-tool admission, already-admitted effects, delayed/failed durable writes,
requester cancellation, restart/lost ACK, wrong scope, exact ASR Stop, read-only
reconciliation, and Node fake-clock client deadlines/ordering. Ten synthetic real
loopback callback scenarios pass without credential exchange. Node syntax and
git whitespace checks pass. Native/browser physical interaction remains separate.
