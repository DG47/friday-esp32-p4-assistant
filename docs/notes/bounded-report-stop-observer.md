# Bounded report Stop observation — server candidate

This is a separate candidate based on2c2bc89. It does not enable reports, Telegram, bot
setup, folder enrollment or broad Mac automation. It adds no live runtime/device write.
The one-second/250ms figures below are observer budgets on a responsive event loop,
not hard termination deadlines or an end-to-end device Stop SLO.

## Protocol and authority

Existing paired-device/owner binding and an exact reportstop token select task and
generation. Generation is checked again inside the durable Stop transaction, closing
the read/check/write race. Existing stale direct controls still fail; the observer can
return the same prior action observation idempotently without reissuing authority.

After existing authorization, start an owned durable revocation task. Observe persistence
for at most1 second. Only a confirmed committed generation change can say authority
revoked. A timeout/storage failure says Stop not confirmed; work may continue. The task
and continuation remain retained even if the requesting coroutine is cancelled; they are
not cancelled/forgotten by the acknowledgement deadline. Pending attempts are capacity
bounded and never evicted to admit more revocations.

After confirmed commit, signal cancellation best-effort and observe the registered owned
handler using asyncio.wait for250ms. A resistant handler or admitted thread can outlive
this window. Acknowledgement reports state at revocation and whether the owned handler
was observed drained, never effect reversal. Later normal status/recovery provides the
durable stopped/uncertain state. Planning/model cancellation is not proof daemon compute
stopped. A task already terminal causes no new revocation claim.

The service tracks planning/execution runners until real completion. Child Stop
cancellation cannot kill its supervisor. Service lifetime/lock ownership is retained
until outstanding runners and Stop persistence/observations drain; shutdown itself has
NO hard deadline here. Admitted store I/O is observed with asyncio.wait and its error
consumed after drain, avoiding shield-future diagnostics while retaining non-cancellation
of the native thread. It is never published as stopped just because an observer timed out.

Same-device new report/generic work is conservatively refused while its Stop attempt or
selected effect outcome is unresolved; explicit read-only report status/recovery remains.
Revocation atomically records an additive report_device_stops binding (device,owner,task,
generation) in the existing report DB. Restart consults that ledger plus the existing
device-view binding and stopping/uncertain state, not
absence of in-memory observers, to retain quarantine. Changing views cannot drop the ledger gate; another device with the same owner is not
blocked without its own binding. No identity is inferred/merged.
No automatic replay, clear, forced deletion or replacement effect authority is introduced.
Already-admitted report bytes remain with write_intent=may_exist and no artifact publication.

The acknowledgement names its exact opaque report task ID, including an idempotent
old-action retry, so it cannot be mistaken for Stop of a different selected task.
The native route returns a fixed acknowledgement via existing error/rejection messaging;
it does not append an unbounded database-recovery read to the observer. Existing scope
priorities remain and no unrelated protected view is replaced. Later status can still
publish verified task state. Telegram/admin Stop paths are not advertised as using this
new observer contract; those integrations stay disabled/deferred.

## Evidence

Nine new production service/controller regressions cover resistant pre-write work,
persistence delay, caller cancellation, storage failure, stale generation/other owner,
already-admitted fsync-held bytes, same-device admission/read-only access and restart
without drain proof. The old runtime Stop method fails the persistence-budget regression
by timing out rather than returning an explicit unconfirmed result. The native-write
case confirms bytes are retained, artifact is not published, and stopped arrives only
after real handler drain. Existing direct owner/stale control regression is preserved.
The supported native route fixture now checks the explicit revocation acknowledgement
before waiting for durable stopped result, instead of treating acknowledgement as failure.
Standalone aggregate **650 tests pass**; exact-head CI belongs in the PR. No model or real account is
called. Timings/gates are synthetic observations, not real-time scheduling guarantees.

## Architectural blockers and release limits

This server component does NOT complete the full proposed device/Mac Stop contract:
- Pairing authorization still includes a synchronous existing SQLite check BEFORE the
  observer budget; event-loop/storage stalls there can delay any acknowledgement.
- Existing peer delivery can consume its own timeout after the observer returns.
- A disconnected/suspended client still needs a bounded local pending/unconfirmed display;
  native/browser timeout UI and physical acceptance are not added or established here.
- Generic AgentJobWorker Stop, remote/model termination and universal dependency inference
  are not implemented by the report-specific observer. Hard Stop deadlines remain unproven.

Do not merge/deploy under a claim that these blockers are solved. Review this as a bounded
server component, with the client/auth/delivery work explicitly outstanding. Keep report
and Telegram flags false. There is one additive report_device_stops table and no existing data rewrite or firmware
flash. It is created only with the already-gated ReportStore. Preserve it with the report
DB backup; older code ignores it and loses the device admission gate. Because report
scope/executor digests bind source, existing approved report scopes may require a fresh
exact review after source change; never rewrite approvals to silently preserve authority.
The sole live owner controls backup, deployment and physical acceptance.


Coordinated integration review adds a same-device task hold independent of the bounded
observer cache. A completed failed/unconfirmed revocation can be evicted from that cache
without allowing new work while the original task remains running/planning/stopping/
uncertain. The hold is scoped to existing owner/device/task identities and clears only
after observed terminal task state, not cache eviction or view selection. A regression
forces capacity-one eviction after a storage failure and switches views. This volatile
hold is not claimed durable when storage never accepted any write; after process loss,
unknown effects still require owner reconciliation and existing no-replay recovery.
