# Friday roadmap and review checklist

This file is the durable checklist for open work. Check an item only after the
relevant automated verification passes and, for hardware behavior, the physical
P3A has been tested.

## Security and connectivity

- [x] Authenticate and encrypt the device/backend WebSocket with TLS and a
  device-pinned Friday CA.
- [x] Persist the last authenticated backend address and prefer it over UDP
  discovery on later boots.
- [x] Remove the unconditional loopback and `testclient` WebSocket
  authentication bypass; give the browser simulator an explicit credential.
- [x] Add device credential revocation and rotation.
- [x] Document certificate and credential recovery/rotation procedures.
- [x] Restrict the simulator credential to loopback and its configured device
  identity.
- [x] Require authenticated loopback access for private calendar and reminder
  read APIs.
- [x] Bind each pairing window to the intended device ID instead of enrolling
  whichever unpaired device connects first.
- [x] Fall back to authenticated discovery after repeated failures to reconnect
  to a saved backend address.
- [ ] Physically verify that authenticated rediscovery switches Friday to a
  backend whose LAN address changed.
- [x] Reduce idle WebSocket write pressure and verify that the recurring
  `transport_poll_write(0)` disconnect does not reproduce during a bounded
  physical idle/recovery check.
- [ ] Run an extended physical soak/load test before treating
  `transport_poll_write(0)` stability as production-proven for long-running
  operation.
- [ ] Require a short code shown by the physical P3A when opening a first-time
  or rotated pairing window; a public device ID alone must not authorize
  enrollment.

## Agent durability and concurrency

- [x] Persist structured tool calls/results so future turns can use exact prior
  calendar and reminder data.
- [x] Persist mock-mode conversation exchanges too.
- [x] Add a maximum job age and mark late results instead of presenting stale
  answers as current.
- [x] Allow bounded parallel job execution without running two turns for the
  same device concurrently.
- [x] Add confirmation before consequential tool actions.
- [x] Generate provider-safe deterministic tool-call IDs for confirmed action
  history.
- [x] Expand natural spoken confirmation and cancellation vocabulary.
- [x] Let unrelated requests proceed normally instead of trapping every turn
  behind a pending confirmation.
- [x] Treat confirmation words as decisions only when the whole utterance is a
  confirmation, so requests beginning with `okay`, `sure`, or `yes` cannot run
  a pending destructive action.
- [x] Tell the user when a new request supersedes and drops a pending action.
- [x] Migrate stored legacy tool-call IDs to the provider-safe `call_<24 hex>`
  format before sending conversation history to Claude or OpenAI.
- [ ] Run a live Claude confirmed-action turn after the stored-history migration
  when an Anthropic API key is available.

## Reminders

- [x] Add cancel, snooze, and complete reminder tools.
- [x] Honor `expires_ms` on physical-device surfaces and return expired alerts
  to idle.
- [x] Treat `reminder.ack` as user acknowledgement, not merely receipt; require
  a tap before marking a reminder seen.
- [x] Add a retry/expiry policy so permanently unacknowledged reminders do not
  retry forever.
- [x] Apply risk-tiered confirmation: create and snooze explicit reminders
  immediately, while cancel and complete remain confirmed actions.

## Provider configuration

- [x] Revisit automatic provider preference instead of implicitly preferring
  OpenAI for short prompts when both cloud providers are configured.
- [x] Review the default Anthropic model and output-token limit against the
  currently supported models before enabling it in production.

## Product roadmap

- [ ] Replace the mock calendar with an authorized real integration.
- [ ] Add durable user-profile and cross-topic long-term memory.
- [ ] Add a sandboxed browser/computer-use capability with explicit approval
  boundaries.
- [ ] Add a dedicated assistant email identity and authorization policy.
- [ ] Move wake-word detection on-device.
- [ ] Attach the external speaker, add TTS, and support interruption/barge-in.
- [ ] Add a streaming STT option if lower latency is needed.
- [ ] Add metrics, alerting, backups/retention, and load/failure testing.
