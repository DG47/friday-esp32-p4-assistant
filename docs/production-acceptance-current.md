# Friday production acceptance — 2026-10-16

This checklist supersedes historical configuration claims in production-readiness.md.
Baseline: main 745bdf41. Scope: one local-only personal assistant spanning the device
and Mac. This is a release gate, not a declaration that passing tests establish readiness.
No live state, credentials, folders, permissions or device writes are included here.

| Requirement | Status | Evidence and acceptance gap |
| --- | --- | --- |
| Installed recovery/peer source preserved | Passed | PR11–13 main retains installed cd58 recovery/peer changes. Wi-Fi repair is merged; owner reports app-only installation. |
| Stale worker outcomes cannot replace current claims | Failed on baseline; repaired in this candidate | Actual workers reproduce obsolete success, failure, timeout and local-model failure overwriting newer state/presentation. Atomic status/attempt/delivery-incarnation fences suppress obsolete store writes and terminal callbacks. Tests also cover uncertainty and reused request IDs. Exact release/CI belongs in the PR. |
| Conservative cancellation/restart | Passed, bounded scope | Existing regressions retain uncertainty markers and do not replay shielded effects after cancellation/restart. A resistant handler can outlive the timeout; this is not a hard execution deadline or proof remote effects stopped. Repeated cancellation, process loss before persistence and multiworker execution are not universally established. |
| Reconnect/result ownership | Passed offline; physical untested | Durable result IDs/ACK replay and experience epoch/request/sequence tests protect negotiated presentations. Legacy clients have weaker delivery semantics. Actual device authentication is still unverified after Wi-Fi settings were found absent by the owner. |
| Continuous contextual device/Mac experience | Partial; end-to-end untested | Existing tests cover scoped event/place corrections, TTL, protected views, stale ownership, dismissal and native reentry. WorkingContext is bounded volatile state keyed per device; Experience ownership is also process-local. Durable conversation/jobs/memory do not establish one automatically shared, restart-persistent workspace across distinct client identities. After restart, expired/current tool facts must be reread. Do not persist private context or unify identities without an explicit design. |
| Calendar clocks and current-read provenance | Passed for bounded deterministic paths; general reliability failed/unresolved | Prior actual-local acceptance captured 18:55 UTC incorrectly paraphrased as 13:55 EDT (correct 14:55 EDT), then stale history/reread failures. ZoneInfo clock facts and fresh-read routing passed later synthetic/real-local reruns including midnight and DST. One passing rerun does not erase earlier failures. Unsupported named/date selectors clarify; arbitrary model paraphrases are not comprehensively gated. |
| Weather corrections and truthful source state | Passed offline; model prose unproven | Existing working-context tests preserve approved ZIP corrections, invalidate changed source/expired facts and honor local-only Calendar sharing. Direct Weather response uses observed readings. Broader model-authored synthesis can still be wrong; current actual-service/read/model acceptance is not rerun here. |
| Free-only operation | Passed configuration/tests; deployment separately owned | Local-only policy fails closed and paid-provider constructors are guarded in tests. No new model download or inference was performed for this audit; model daemon cancellation/resource caps are not proved by coroutine cancellation. |
| Report/Telegram | Deferred/off | Default flags stay false; no real project enrollment, bot setup or external account call. Prior real-local dispatcher acceptance used synthetic files and mock Telegram. This does not establish live bot/phone acceptance. |
| Physical voice/display/touch/Stop/soak | Untested here | Native captures and C tests are not acoustic, touch, authentication or panel acceptance. Owner controls hardware and runtime checks. |
| General Mac automation | Deferred | No broad permissions, folders, shell execution, new credentials or automation enrollment added. |

## Current repair semantics

An asynchronous completion/failure must atomically match the running claim's ID,
attempt count and existing result_delivery_id, and must not replace an uncertainty
marker. A timeout marks uncertainty only if its claim still owns that row. An obsolete
handler cannot deliver its result, reset idle or invoke on_finished. Prune/recreate
with the same request ID gets a different delivery ID. No schema migration is needed.
Trusted legacy by-ID complete remains a fixture/admin compatibility path; all actual
AgentJobWorker completions supply the claim. This is not external exactly-once
execution, a cross-process scheduler lock or protection against restoring the same
old claim incarnation from a backup. Ordinary authorized current-claim retries remain.
Persistence failure retains the existing in-memory non-replay fence; it cannot ensure
that fence survives process loss.

## Release/operation sequence

Require exact-head aggregate backend tests, native workflow checks and CI before merge.
Record exact commit and CI to the coordinating owner before merging. After merge,
verify exact-main CI and send the source SHA plus migration/rollback notes. Only that
owner deploys. Preserve the absolute DB, local-only policy, model/TTS configuration,
trust material, pairing/NVS and report flags. Do not clear uncertain rows to make the
queue responsive. This backend-only fix needs no firmware flash. Physical and factual
model acceptance remain gates even when source CI passes.


## Uncertain-task restart visibility candidate

Reconnect now offers a fixed honest uncertainty review and refuses new same-device
admission instead of showing work as accepted behind an unresolved claim. No marker is
cleared or effect replayed. Text/voice/controller regressions cover this boundary; see
uncertain-task-recovery.md. Throughput remains deliberately blocked until private owner
reconciliation; this is not universal cancellation or automatic task completion.


The same admission candidate rejects terminal request-ID replay before claiming busy/
thinking state. A route regression covers ACK and subsequent Reminders navigation while
preserving one timer, attempt and delivery identity. Historical result redaction remains;
no old result is rebound as current. This addresses the owner's concrete retry failure,
not a hard deadline for cancellation-resistant handlers or broad factual-model reliability.
