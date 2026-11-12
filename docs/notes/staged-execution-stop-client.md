# Staged native execution Stop client

Independent source candidate based on b751ece, for the separately reviewed
execution_stop_v1 server draft (PR20). No live source, service, Wi-Fi, credentials,
NVS, CA, settings or hardware was written. This is not a firmware deployment.

CONFIG_FRIDAY_EXECUTION_STOP defaults off and depends on the now explicit,
default-off FRIDAY_EXPERIENCE_CONTEXT option. New capability is advertised only
with the context renderer enabled. No old firmware is treated as supporting it.
Authenticated readiness must negotiate execution_stop_v1 before controls work.
No browser simulator is used as native rendering evidence.

The fixed-size C controller retains exact job/incarnation/action/query identities.
A local monotonic deadline starts at touch Stop or an exact recognized Stop intent;
recognition time itself precedes that deadline. The backend samples every 100ms,
and the LVGL notice timer also samples every 100ms; this is not a hard physical
one-second SLO. Pending, unavailable transport, unconfirmed, revoked/uncertain,
verified pre-admission Stop and terminal-with-prior-effects are distinct states.
Handler drainage never means Stopped. Strict target/action/bool/state validation
rejects stale and contradictory observations. Retry reuses the original action.

Stop/status controls are 168x48 at (504,76)/(504,132). Pending and already-revoked
Stop controls are disabled; explicit retry is available after unconfirmed delivery.
Read-only status remains available online. The existing reserved top notice band
carries the outcome, separately from complete protected facts and scene actions.
No ordinary scene/model action implies Stop or confirms consent. Existing glyph
normalization and full fact/priority/lease/reentry guards remain in use.

Transport captures an accepted native job incarnation, intercepts control replies
outside presentation routing, and keeps pending outcomes across disconnect.
Reconnect sends only read-only status; it never resubmits Stop. Metadata-only
server recovery can restore an uncertain target after reboot without new NVS data.
Audio activation snapshots the explicit target; exact recognized Stop uses that
activation action identity instead of requesting another agent job. New mutable
scene actions are held while unknown. Already-admitted native/remote work may
finish; local audio suppression does not prove its remote producer terminated.

Validation uses only synthetic content and local existing dependencies:
- Strict C host tests plus ASan/UBSan cover deadline boundaries, disconnect, stale
  action/incarnation, lost ACK, retry identity, result-before-ACK, unknown handler
  drain, contradictory status, restart recovery and pre-admission-only success.
- Actual LVGL/RGB565 renderer passes the full existing native harness with the
  feature enabled and disabled, including full memory review, reminder priority,
  ownership, long text, lease/cancel/reentry and Weather/media guards. New captures
  check all six states, 720px clipping, 48px targets, disabled states, touch routing
  and 1000 pending/disconnect cycles. New controls add 2040 settled host heap bytes:
  43880 used/82616 peak versus 41840/81080 without them. These are host metrics,
  not panel FPS, DMA/PSRAM, fragmentation or physical touch/recognition evidence.
- ESP-IDF v5.5.5 builds the actual ESP32-P4 source with both options enabled:
  application 0x422e50 bytes, 48% of 8MiB app partition free. Candidate defaults and
  repository CA were used, never live configuration. This binary is not ready to
  flash into the installed environment. Component lockfile remains unchanged.

Synthetic native contact sheet is saved in Library as execution-contact.png
(libfile_19bedca031d08191b4a627710e61f8e5). Source capture frames are local outputs,
not physical-panel photographs. The existing native glyph-test CI entrypoint also runs the strict C/sanitizer controller regression, using its already-installed libcjson dependency; no workflow-file modification or new app permission is needed.
CI does not claim a full ESP-IDF build or physical socket/voice acceptance.

Rollout remains separate: first coordinate exact reviewed server/client revisions,
then the sole live owner must rebuild with preserved installed configuration/CA,
confirm board/partition compatibility, and obtain separate physical rollout scope.
No flash is requested here. Physical Wi-Fi authentication remains unverified since
Oct13; panel/touch, socket+voice timing and sustained interruption need actual
owner observation. No-tool factual correctness, hard external-process termination,
and safe unknown-effect clear/replay are not established by these source tests.
