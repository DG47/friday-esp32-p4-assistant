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


## Terminal request replay and presentation liveness

The runtime owner's controlled restart/ACK reproduction found that retrying a completed
request ID retained one timer and one attempt, but the handler still claimed new
thinking/busy state. No worker remained to finish that row, so Reminders navigation
was rejected as interaction_pending. The source route regression reproduces acceptance
on the old handler and exercises the repaired route with actual WebSocket admission,
result ACK and subsequent Reminders navigation.

A terminal row returned by idempotent enqueue is now rejected before binding presentation,
claiming navigation, cancelling speech or sending thinking/job.accepted. Completed,
failed and expired rows cannot be presented as newly executing work. The fixed response
states that no action was repeated and a new request is needed; it does not rebind an
old result as current. Stored timer/deadline/result/delivery identity and one attempt
are preserved. Existing durable ACK/reconnect delivery remains independent. Typed
admission errors are caught for text, touch and voice so this refusal does not close
the socket. This removes a false busy state rather than clearing a genuine running job.
