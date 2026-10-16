# Honest uncertain-task recovery after reconnect/restart

The durable recovery marker correctly prevented automatic effect replay, but reconnect
reported nothing to recover and a new same-device utterance was accepted into a queue
that could not run. A baseline store regression confirms that this admission was allowed.

The candidate includes uncertain running jobs in the authenticated recovery affordance.
Explicit Recover shows only a fixed outcome-unconfirmed explanation, without action text,
confirmation buttons, consent or automatic reconciliation. Existing memory/ordinary
confirmation priorities remain. A new enqueue is atomically rejected while that device
has an uncertain running row, before a new row, thinking state or job.accepted is created.
The same applies to a replay of the uncertain request ID. Other devices can still work.
Existing queued rows/uncertain claims are not deleted, cleared, cancelled or replayed.

Text, touch mutation and voice admission propagate the safe uncertainty code. Voice
rejection is caught without disconnecting the socket. Controlled tests verify reopened
storage, private-text-free authenticated reconnect/Recover, rejected text and voice,
no new row, preserved marker and another device's admission. An existing shielded-effect
fixture now expects admission refusal instead of silently queueing more work. The result
is honest unavailability, not successful effect cancellation or restored throughput.

The owner must privately reconcile the real effect outcome. No new clear/retry endpoint,
operator permission, automatic action, broad Mac automation or report/Telegram activation
is added. This preserves the conservative single-worker guarantee; process loss before
marker persistence, remote effects and multiworker scheduling remain outside universal
protection. No schema or firmware change is required. Exact-head tests/CI belong in the
PR; actual physical reconnect/voice acceptance remains with the coordinating owner.
