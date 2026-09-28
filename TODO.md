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

## Agent durability and concurrency

- [x] Persist structured tool calls/results so future turns can use exact prior
  calendar and reminder data.
- [x] Persist mock-mode conversation exchanges too.
- [x] Add a maximum job age and mark late results instead of presenting stale
  answers as current.
- [x] Allow bounded parallel job execution without running two turns for the
  same device concurrently.
- [x] Add confirmation before consequential tool actions.

## Reminders

- [x] Add cancel, snooze, and complete reminder tools.
- [x] Honor `expires_ms` on physical-device surfaces and return expired alerts
  to idle.
- [x] Treat `reminder.ack` as user acknowledgement, not merely receipt; require
  a tap before marking a reminder seen.
- [x] Add a retry/expiry policy so permanently unacknowledged reminders do not
  retry forever.

## Provider configuration

- [ ] Revisit automatic provider preference instead of implicitly preferring
  OpenAI for short prompts when both cloud providers are configured.
- [ ] Review the default Anthropic model and output-token limit against the
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
