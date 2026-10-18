# Active and uncertain agent-job recovery

This source integration extracts the intended recovery delta from installed-lineage
candidate `ab85d5f` onto GitHub main `3e1baf9`. It does not replace main with the older
installed tree: merged report/Telegram modules and their tests are retained. The runtime
owner deployed its installed-lineage repair separately; this PR changes no runtime,
configuration, database, pairing, NVS, certificate, network or firmware.

## Defect and repair

A synthetic blocked handler reproduced lease maintenance requeuing an active job and
allowing a second claim. The worker now excludes claimed slots, live child handlers and
local persistence-failure fences from its own recovery pass. There is no event-loop
yield between claiming a job and registering local ownership.

Cancellation or timeout persists `interrupted_execution_outcome_uncertain` in the
existing error column, retaining status `running`. Recovery excludes that marker even
after the store is reopened. Timeout sends a fixed uncertainty message rather than
completion or automatic replay. A cancellation-resistant handler remains locally owned
until actual return; wait_for cancellation is not treated as proof that native,
shielded or remote effects stopped. The offline deployment fixture checks conservative
non-replay instead of expecting a cancelled effect to run again.

## Deliberate blocking and limits

An uncertain running claim intentionally blocks subsequent jobs in that device's
generic agent queue until explicit reconciliation of the real outcome. Do not clear
markers or repeat external actions merely because the lease expired. No automatic
reconciliation endpoint is introduced. The report task store is a separate existing
state machine with its own approval/fence/Stop rules; this repair does not modify it
or claim a global lock across every subsystem.

This is not universal exactly-once execution. A crash before marker persistence,
recovery by another worker/process, or a persistence failure followed by process loss
remains outside protection. Persistence failure retains an in-memory fence in the
current worker. Repeated parent cancellation is not directly covered by the supplied
five regressions. Ordinary exception retry policy is unchanged.

## Validation provenance

The installed-lineage suite reportedly passed 500 tests, including five new ownership
regressions. That result does not cover the merged feature tree. This integration runs
the full main-based suite separately; the scoped tests cover active lease expiry,
cancellation-resistant timeout, restart-persisted cancellation, shielded effects after
parent cancellation and shielded effects after timeout. The five focused tests and the initial full **614-test integrated suite passed**, with zero
failures, errors or skips. The actual updated recovery fixture function also passed
independently with its database path redirected to temporary synthetic state; its
container/TLS harness was not rerun. Independent read-only review found no concrete
blocker within the documented scope. Exact-head CI is recorded in this PR. Only
original synthetic state and providers were used.

## Separate device connection gate

The runtime owner reports an advertised LAN endpoint/certificate address mismatch.
Certificate renewal remains separately awaiting the owner's approval. This source PR
makes no certificate/key, TLS-verification, address, network or credential change and
cannot restore connectivity by itself. Physical panel, touch, microphone, speaker and
Stop acceptance remain unverified. No physical acceptance is inferred from source
integration or CI.


## Integrated lifecycle failures found by CI

The first main-based PR run passed 614 tests, but duplicate push attempts stalled.
A bounded CI watchdog exposed two integration failures: a closed AnyIO WebSocket stream
could escape peer delivery and terminate the report supervisor, and shutdown could wait
for a report loop that had no cooperative stop signal before cancellation.

The additional narrow fixes disconnect closed/broken/end-of-stream peers without
suppressing cancellation, signal the shared report stop event before task cancellation,
and reject new report/admin admission once stopping. Report approval, file execution,
Telegram owner binding and task-state semantics are preserved. Deterministic regressions
failed before these fixes and passed afterward; healthy-peer delivery remains available.
The combined suite now passes **617 tests**. Independent read-only review found no
concrete blocker within scope. The CI wall timeout and stack dump expose future stalls;
they do not prove universally bounded native/remote shutdown or effect cancellation.
No runtime or certificate change was made by this publication.


## Installed peer-path parity follow-up

Installed source `cd58f476` also contains the narrow AnyIO closed-stream catch in
targeted media delivery. This follow-up restores that path on merged main: closed,
broken and end-of-stream peers are disconnected and return zero deliveries. Healthy
peer behavior, send locking, ownership checks and cancellation propagation are
unchanged. Regressions cover each of the three errors in targeted sends and explicit
peer close. The targeted-send regression fails for all three errors on pre-fix main.

Direct comparison confirms the resulting DeviceHub and existing agent_jobs source
files are byte-identical to installed cd58f476. No report/Telegram source, defaults,
configuration, runtime state or firmware changes are included. Exact-head full-suite
and CI results are recorded in the follow-up PR.
