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
parent cancellation and shielded effects after timeout. The five focused tests and the full **614-test integrated suite passed**, with zero
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
