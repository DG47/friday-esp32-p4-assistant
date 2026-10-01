# Friday release readiness — 2026-09-30

This change is a hardened local release candidate, not a declaration of
production readiness or an internet deployment. Work is isolated from the live
Mac checkout. Do not replace its `.env`, SQLite database, TLS files, or firmware
identity with anything from this checkout.

## Product behavior

The device remains ambient until asked. Context scenes put the artifact first,
retain a small Friday presence, and collapse after their TTL or dismissal.
The verified board configuration is a 720×720 Waveshare ESP32-P4 display with
GT911 touch and LVGL 9. Four readable rows and up to four contextual buttons
replace the previous title/subtitle-only renderer. Long-press the orb for local
status; normal tapping retains the existing voice and reminder-acknowledgement
behavior. Scene items are not discarded when `assistant.text` follows them.

Working local controls:

- Say `show status`, `show my reminders`, or `show calendar`. Long-pressing the
  physical orb opens status; the simulator provides equivalent local-view controls.
- Touch a reminder row to open its details. Snooze moves it by ten minutes.
  Complete and cancel enter the existing durable confirmation workflow; say yes
  or no. They do not bypass confirmation. Repeated mutation taps are suppressed
  while the current action is pending; an error permits retry.
- Say `set a timer for 2 minutes` (seconds/minutes/hours, up to 24 hours). This
  creates an idempotent durable reminder and displays a countdown. Dismissing
  the scene does not cancel the reminder. Delayed delivery recomputes the
  countdown from the persisted deadline instead of restarting it. Manage the
  timer through its reminder details. Due alerts retain tap acknowledgement.
- The browser can acknowledge a displayed reminder explicitly; mere receipt,
  dismissal, or expiry does not mark it delivered. Tapping the voice control
  stops browser speech playback before starting another request.

Calendar is sample data and is labeled accordingly in local calendar scenes.
Gmail, real Calendar, Spotify, Home Assistant, computer control and web
adapters have not been connected. No buttons imply those integrations work.
The protocol reserves additional surface kinds for later adapters; map, graph,
media, email, camera and artifact-specific renderers are not implemented by
merely adding those enum values. The physical fallback is safe text/cards.
Physical TTS/speaker playback and audio-synchronized animation remain missing.

## Protocol additions and bounds

`scene.action` accepts only a validated local action ID. For example:

```json
{"type":"scene.action","action":"reminders"}
{"type":"scene.action","action":"reminder:12"}
{"type":"scene.action","action":"snooze:12","request_id":"unique-touch-id"}
```

Reminder IDs are checked against the authenticated device's active reminders.
Mutations enqueue ordinary durable turns under the supplied request identity.
They do not accept arbitrary tool names, shell commands or URLs. Surface
`actions` contain validated `{id,label}` values, with at most four entries.
A timer can carry `deadline_at` plus a bounded `countdown_ms`; firmware uses a
monotonic countdown rather than assuming its wall clock is synchronized.

Control messages are limited to 16 KiB, utterances to 4096 characters and PCM
frames to 1 MiB with complete 16-bit samples. Authentication has a fixed
10-second deadline. At most 128 socket connections and four authenticated
peers per device are admitted. A device can queue at most 32 unfinished jobs;
replaying an existing request ID still succeeds at the limit. Peer writes are
serialized and bounded by a timeout so a stalled display does not stall every
other peer. Worker and maintenance loops recover from transient infrastructure
errors. Identity revocation/rotation closes already authenticated peers.

Tool-calling providers emit visible working/completed/failed progress without
including private tool arguments. Production configuration fails closed if it
uses mock/auto mode, missing or reused admin/simulator credentials, or plain WS.
Validation errors suppress credential-bearing input values. Browser credentials
remain in page memory; they are no longer written to localStorage.

## Operations

Use `compose.production.yaml` as a standalone profile; do not merge it with the
development Compose file. It removes source mounts/reload, runs as UID 10001,
uses one process, a read-only root filesystem, dropped Linux capabilities,
bounded logs, persistent data/model volumes and TLS. It does not deploy by
being present. Validate without resolving actual environment values:

```sh
docker compose -f compose.production.yaml config --no-env-resolution --quiet
```

Before an approved deployment, make the mounted TLS files readable by UID
10001 while preserving restricted key permissions. Never fix permission errors
by making private keys world-readable. Existing root-owned Docker data volumes
may need an explicitly approved ownership migration. Provision Whisper models
before operating without outbound network access; first model use may otherwise
download weights. The image has not been built or run in this change.

Use one backend process/replica: the socket hub, UDP discovery and reminder
routing are process-local. SQLite job leases alone do not make multi-process
socket delivery safe. A managed server needs a deliberate certificate/key
lifecycle, network boundary, storage backups and monitoring before release.

- `/health` is liveness. `/ready` checks background workers and database
  connectivity, returning 503 if unavailable.
- `/api/diagnostics` is admin-authenticated and loopback-only. It returns job
  status counts, connected device count and worker health, not conversations,
  reminder text, credentials or provider payloads. Connect an approved monitor
  to readiness and terminal job counts; no external alerts were configured.
- Back up SQLite with its online backup API, not a raw copy of a live database:

```sh
python3 scripts/backup_database.py /path/to/friday.db /path/to/new-backup.db
```

The destination must not exist; backups are created with mode 0600 and checked
for integrity. This command has only been tested against synthetic databases.
Restore into a new location, verify integrity/schema, then switch a stopped
service deliberately. Keep an encrypted/off-host backup and test restore before
release. No live data was copied and no retention deletion was performed.
Choose retention for reminder history, conversations and terminal jobs before
long-running use; storage remains unbounded over the life of the service.

## Verification and release gates

Automated evidence is recorded under `outputs/qa/`. The baseline had 85 passing
backend tests. Added tests cover input rejection, production fail-closed and
credential-safe validation, bounded peer writes and queue admission, scoped
scene actions, timer idempotency/restart/delayed delivery, restart-safe reminder
confirmation, worker recovery, readiness, backup restoration and a 96-job,
12-device mock load preserving per-device order and four-way concurrency.

Browser QA uses installed **Google Chrome in a disposable headless profile**,
loading the actual simulator HTML/CSS/JS with explicitly labeled synthetic
fixtures. No signed-in browser or live service was used. Fixture assertions
exercise contextual dispatch, duplicate taps, interrupted retries, offline
controls, retained actions, countdown and TTL dismissal. Screenshots verify
layout only; they are not physical-display evidence. The browser-control Node
execution tool and Computer Use tools were absent in this delegated environment;
headless Chrome was the safe available alternative. The isolated Chrome process
and profile are cleaned up by `scripts/visual_qa.py`.

Before claiming production readiness:

1. Approve deploying/flashing this candidate, then verify physical text/card
   layout, long-press/tap separation, reminder controls/confirmation, timer,
   scene expiry, reconnect and wake/VAD under a physical soak. No flashing or
   hardware interactions were performed for this change.
2. Build/run the production container and verify non-root TLS/data access,
   model availability, restart/health behavior and backup restore on its volume.
3. Validate the chosen provider and model with authorized live calls; all new
   provider-dependent tests used mocks.
4. Select and authorize actual integration adapters, including real calendar;
   define their permissions and destructive-action confirmations. No app
   credential was read, changed or created.
5. Complete physical speech playback/barge-in, key storage/NVS encryption,
   certificate rotation/recovery, monitoring/alerting and a retention policy.
6. Physically validate result acknowledgement/reconnect behavior. The code gate
   is closed with durable delivery IDs and UI acknowledgements; see the bounded
   delivery guarantee below. Legacy clients retain send-only delivery.

The ambient-first foundation is now usable for local capabilities. The broader
cross-service assistant still has these material release gates.

Final candidate verification: 127 backend tests passed; ESP-IDF firmware build
passed; JavaScript syntax and Python compilation passed; production Compose
configuration validated. Disposable headless Google Chrome verified twenty touch/
action/recovery/layout/synthetic-speech checks at a 500px viewport. Desktop and narrow screenshots
use explicitly labeled synthetic QA fixtures. No physical device or live provider
was exercised.

## Follow-up software work: voice and weather

The candidate now includes an opt-in OpenAI PCM TTS adapter, using the existing
SDK dependency. `TTS_PROVIDER=none` remains the default. Enabling it requires an
explicit provider decision/authorization and a locally supplied key; no key was
read, changed or created. `TTS_MODEL` and `TTS_VOICE` are configurable (`tts-1` /
`onyx` defaults). Text is bounded to 1,000 characters and playback to 30 seconds;
provider generation has a 35-second deadline and four synthesis slots. The
adapter follows [OpenAI's documented PCM format](https://developers.openai.com/api/docs/guides/text-to-speech).

The ESP32 speaker path uses the board BSP's ES8311 output on the shared 24 kHz
bus, stereo duplication of mono PCM and a conservative 40% volume. Only a
successfully initialized speaker advertises `pcm_speech`. Each utterance has a
unique stream ID, bounded 4,800-byte ordered chunks, exact byte count and a
completion/failure acknowledgement. A single assembling buffer, one queued
utterance and one writer-owned utterance bound storage to 4.32 MB of PSRAM in
the worst overlapping case; allocation failure is reported, never an unbounded
queue. Cancellation frees the assembling/queued buffers, invalidates the active
writer by epoch and ignores late chunks without an active matching stream.
The codec task checks cancellation before each 20 ms write. Actual interruption
latency and shared-bus behavior remain physically unverified.

Touch activation stops local playback before microphone capture and cancels
server synthesis/delivery. New queued requests suppress speech for superseded
job IDs. Disconnect cancels both device playback and backend tasks. Browser
speech follows actual engine start/end/error events, cancels on touch or typed
input, and ignores stale completion callbacks. Synthetic Chrome tests exercise
these transitions; they do not prove browser microphone permissions or audio.
Firmware wake input is suppressed during playback to avoid self-triggering.
**This is touch interruption, not acoustic barge-in.** Acoustic barge-in still
needs a tested echo-reference/AEC path and physical speaker/microphone evidence;
no guessed echo-cancellation implementation was added.

A read-only Open-Meteo adapter now backs `show weather` and the model's
`get_weather` tool. It uses a fixed HTTPS endpoint, an eight-second timeout,
64 KiB response bound, explicit units, validated ranges and stale-data rejection.
Its real scene shows temperature, humidity, wind, source and UTC observation
time. `WEATHER_PROVIDER=none` remains the default. Opt-in requires explicit
latitude/longitude and permission to send that location to the provider; no
location was inferred and no live request occurred. Review the provider's
[usage terms](https://open-meteo.com/en/terms) before commercial release.

| Gate | Exact remaining blocker |
| --- | --- |
| Local candidate activation | Approval to apply patch and restart original backend; neither done |
| Physical release | Approval to flash, then speaker/shared-bus, touch, wake, reconnect and soak verification |
| Synthesized device voice | User-selected/authorized TTS provider configuration and live contract test; code exists and mock tests pass |
| Acoustic interruption | Missing echo-reference/AEC integration plus physical testing; touch cancellation is implemented |
| Weather activation | Approved location/provider configuration and live test; adapter/scene exist and mock tests pass |
| Gmail / Calendar / Spotify / Home Assistant / computer | Adapter/OAuth/permission code remains missing, then authorized account configuration and live tests |
| Production packaging | Container image build/run, non-root TLS/data-volume access and deployment approval |
| Operations/security lifecycle | Monitoring/alert destinations, backup retention, certificate/key rotation and physical NVS encryption policy/implementation |
| Result delivery | Code and regression tests complete; physical UI/reconnect validation remains; exactly-once physical display is not promised |

Recommended next step: approve applying this candidate and restarting the local
backend with TTS/weather still disabled. Then test the existing local scene and
reminder flows before separately authorizing firmware flashing/provider setup.

## Durable result delivery and actual guarantee

Upgraded browser/firmware clients advertise `result_ack`. Completed and failed/
expired/late results use a single `agent.result` envelope containing a stable
SQLite delivery ID, job ID, status, scene and text. Existing databases migrate
in place; retries/restarts retain the same ID. Clients send `result.ack` only
after their UI APIs accept scene/text. Firmware reports success only after its
scene and response updates acquire the display lock; a failed/partial update
stays unacknowledged and retryable. This does not prove LCD scan-out or that the
user saw the result.

Socket send alone does not mark upgraded results delivered. Acknowledgements
are scoped to the authenticated logical device, job and delivery ID, require a
prior send attempt, and are idempotent. Once a job requires UI acknowledgement,
a later legacy connection/capability downgrade cannot bypass that requirement.
An acknowledgement from any capable peer of that logical device is sufficient;
this is not an acknowledgement from every concurrent physical/browser peer.
Legacy-only jobs retain send-only compatibility.

Unacknowledged results retry every ten seconds while connected, including after
reconnect (which may wait for the current retry interval). The 64 most recent
accepted IDs stay in each client's memory across reconnects; duplicates are
re-acknowledged without re-rendering, resetting scene TTL or replaying speech.
Replay does not repeat tool progress or interrupt listening with an idle state.
The cache is not persisted across process/device reboot: a crash after UI
acceptance but before server acknowledgement, or cache eviction, can present the
result again. Server-side request/tool idempotency remains separate from UI
presentation. Speech and reminder acknowledgements retain their separate meaning.

Terminal delivery records are retained for at most seven days from job creation
and at most 10,000 terminal rows globally; each replay batch has at most 64 rows.
The maintenance worker removes older terminal records without deleting active
jobs, reminder/task state, tool-operation state or conversational history.
The retention limit can discard an offline/unacknowledged result; request-ID
queue deduplication only lasts while its job record is retained. These are
bounded at-least-once delivery attempts until acknowledgement/expiry, with recent
client duplicate suppression—not an exactly-once physical-display guarantee.

Regression tests verify migration stability, restart/reconnect replay, scoped
and duplicate acknowledgement, unsent-result rejection, downgrade protection,
failed-result acknowledgement, age/count/batch bounds, and an authenticated
WebSocket flow. Chrome fixtures additionally verify acknowledgement after DOM
acceptance, rejected-render retry and suppression of duplicate render/speech.
