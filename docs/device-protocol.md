# Device protocol v1

Transport: WebSocket at `/ws/device/{device_id}`. Control messages are UTF-8
JSON. PCM audio frames are binary WebSocket messages associated with either
idle wake-word detection or the active turn on that same connection.

Multiple clients may join the same `device_id`. Server messages are broadcast
to every connected peer for that device, allowing the browser input/debug view
and the physical display to stay synchronized. The default physical device ID
is `friday-p3a`. Loopback clients support the browser simulator. A LAN client
must authenticate before it is registered or allowed to send audio/control
data.

## Discovery and pairing

After joining Wi-Fi, firmware broadcasts `FRIDAY_DISCOVER_V1 <device_id>` on
UDP port 8334. The backend replies directly with protocol version 1 and the
`wss://` URL for the requesting device. Firmware accepts only TLS URLs and
verifies the server against the Friday CA embedded at build time. After the
first authenticated `ready`, it persists the endpoint and prefers it on later
boots, so an untrusted discovery responder cannot redirect an already-paired
device. Its compile-time URL remains a recovery fallback. After five
consecutive reconnects that never authenticate, firmware reruns discovery,
switches the live client to the returned TLS URL, and persists that address
only after the normal authenticated `ready` boundary.

By default, the backend advertises the interface selected for the requesting
device. A multi-homed deployment can set `ASSISTANT_PUBLIC_HOST` to advertise a
specific LAN address; that address must be present in the backend certificate.

On first boot, firmware creates a random 32-byte token and stores it in NVS.
The backend stores only its SHA-256 hash. If enrollment is required, Friday
shows an eight-character code derived from that token. An unpaired LAN device
is accepted only when its ID and code match a two-minute, loopback-opened
pairing window:

```sh
curl --cacert data/tls/friday-ca.crt -X POST \
  -H "X-Friday-Admin-Token: $ASSISTANT_ADMIN_TOKEN" \
  -H 'X-Friday-Pairing: open' \
  -H 'X-Friday-Device-ID: friday-p3a' \
  -H 'X-Friday-Pairing-Code: ABCD-1234' \
  https://127.0.0.1:8000/api/devices/pairing-window
```

Replace the example with the code on the physical display. A client that knows
only the public device ID cannot use the window. It closes as soon as the
matching device enrolls. The stored identity is reused after device and backend restarts. All
WebSocket peers, including loopback clients, must authenticate. The browser
simulator credential is accepted only from loopback for the configured
simulator device ID rather than acting as a global device credential.

`GET /api/calendar/today` and `GET /api/reminders` are loopback-only and require
either `X-Friday-Simulator-Token` or `X-Friday-Admin-Token`. Reminder reads are
scoped to the configured simulator device ID.

Rotate an online physical identity with `X-Friday-Device-Action: rotate` at
`POST /api/devices/{device_id}/rotate`; this clears the device token and
reboots. Wait for the new display code, then use the pairing-window request
above. `revoke` uses the same
endpoint shape but does not open a pairing window. Both actions are loopback
only and require `X-Friday-Admin-Token`.

## Client messages

```json
{"type":"hello","protocol":1,"device_token":"64 lowercase hex characters","capabilities":["display","touch","reminder_ack"]}
{"type":"wake.start","format":"pcm_s16le","sample_rate":12000,"channels":1}
{"type":"activate","source":"touch"}
{"type":"activate","source":"wake_word"}
{"type":"utterance","request_id":"7b1...","text":"What's next?"}
{"type":"audio.start","format":"pcm_s16le","sample_rate":12000,"channels":1}
{"type":"audio.end"}
{"type":"reminder.ack","reminder_id":42}
```

For a voice turn, send `audio.start`, one or more binary PCM frames, then
`audio.end`. The current device sends mono signed 16-bit little-endian PCM at
12 kHz in 20 ms frames, decimated from its 24 kHz capture path. Only one
audio turn may be active per connection; turns shorter than 200 ms or longer
than `STT_MAX_SECONDS` are rejected.

After `hello`, or an explicit `wake.start`, binary PCM received while idle is
treated as a rolling wake-word stream. The firmware retains up to 500 ms of
pre-roll locally and sends `wake.start` when sustained speech opens the gate.
Each window resets buffered wake audio and cancels the previous probe task.
Audio is batched at 500 ms; windows run at least three seconds to fill a probe,
close after trailing silence, and are capped at roughly twelve seconds (batch
alignment may add up to 500 ms). Quiet idle periods send no PCM. Ambient speech
or sustained noise can still open repeated windows; this is an energy gate,
not on-device keyword recognition. The local backend uses faster-whisper to
detect the exact word `Friday`, emits `wake.detected`, and discards any wake
frames that arrive while the device switches modes. The device then sends
`activate` with source `wake_word`, followed by the same `audio.start` and
binary PCM sequence as touch activation. Its adaptive energy VAD detects
sustained speech and sends `audio.end` after 800 ms of trailing silence. A
second tap can still submit manually, and a 12-second cutoff prevents abandoned
turns from streaming forever. The capture boundary remains separate from STT.

## Server messages

```json
{"type":"hello.required","protocol":1,"device_id":"friday-p3a"}
{"type":"ready","protocol":1,"device_id":"friday-p3a","state":"idle","surface":{"kind":"ambient","title":"Friday","subtitle":"Paired and ready."}}
{"type":"wake.detected","wake_word":"Friday"}
{"type":"job.accepted","job_id":"7b1..."}
{"type":"state","state":"thinking"}
{"type":"transcript","text":"What's next?","provider":"faster-whisper"}
{"type":"assistant.text","text":"Your interview is at two."}
{"type":"surface","surface":{"kind":"timeline","title":"Today","items":[]}}
{"type":"tool.activity","name":"calendar","status":"working"}
{"type":"reminder.alert","reminder_id":42,"text":"Reminder: prepare","surface":{"kind":"reminder","title":"Reminder","subtitle":"prepare","expires_ms":60000}}
{"type":"error","code":"bad_message","message":"..."}
```

The server sends `hello.required` immediately after accepting the WebSocket.
The client must answer with `hello`. Only after successful authentication does
the server register the connection and send `ready`. Its ambient surface
explicitly replaces any pairing code with `Paired and ready.`; firmware does not stream
idle audio or enable activation before that boundary.

Utterances become durable agent jobs. Supplying a stable `request_id` makes
retries idempotent; the server returns the corresponding `job.accepted` event
without running the same request twice. Jobs interrupted by a backend restart
are retried after their lease expires, and completed results are retained until
they can be sent to a connected peer. Jobs or offline results older than the
configured useful lifetime produce an `agent_stale` error instead of presenting
an outdated answer as current. Different device IDs can run concurrently, but
turns for one device ID are always serialized.

Destructive reminder tools use the normal message/surface protocol to request
confirmation. Creation and snoozing execute immediately when explicitly
requested; cancellation and completion are stored in SQLite and execute only
after a complete affirmative reply such as `yes` or `go ahead`. Merely starting
a longer request with `okay`, `sure`, or `yes` does not confirm. A negative
response cancels the action. An unrelated request supersedes it, is handled
normally, and receives an explicit notice that the pending action was dropped.

`reminder.alert` is sent only to peers advertising `reminder_ack`. The physical
device renders the alert for `expires_ms` and returns to idle without replying
when it expires. It sends `reminder.ack` only after the user taps the displayed
alert. Until that acknowledgement is stored, the backend retries delivery at a
bounded cadence and stops after the configured maximum number of attempts.

States are `idle`, `listening`, `thinking`, `speaking`, and `alert`. Surface
kinds currently implemented by the simulator are `ambient`, `timeline`,
`reminder`, and `message`.

## Adaptive scene actions

See [the scene/action contract and release limits](production-readiness.md#protocol-additions-and-bounds).
Authenticated `scene.action` messages request local views or operate on one
device-scoped reminder through the existing durable agent/confirmation path.
Surface `actions` are bounded, allowlisted `{id,label}` values; item `action`
values open reminder detail scenes. The timer scene uses a persisted
`deadline_at` and adjusted `countdown_ms`. Older clients may ignore these fields.

## Optional PCM speech capability

A successfully initialized physical speaker advertises `pcm_speech`. TTS is
opt-in and disabled by default. Backend emits `speech.start` (`stream_id`,
`format=pcm_s16le`, `sample_rate=24000`, `channels=1`, exact `bytes` <=1,440,000),
then ordered `speech.chunk` messages (`sequence` starting at zero, base64 `pcm`
<=4,800 decoded bytes), then `speech.end`. Device assembles bounded PSRAM audio
and acknowledges `speech.finished` with the same `stream_id` and
`status=completed|failed`. Mismatched acknowledgements are ignored.
`speech.cancel`, local touch activation and disconnect invalidate playback and
queued chunks; no speech completion is claimed for cancelled audio. Capture is
half duplex; wake audio is suppressed during playback. This protocol provides
touch interruption and does not implement acoustic echo cancellation/barge-in.
Speech is synthesized fully before transfer, so first-audio latency includes
synthesis/transfer; progressive playback is a future optimization.

## Optional durable agent-result acknowledgement

Upgraded clients advertise `result_ack`. Backend sends `agent.result` with
`job_id`, stable 32-character lowercase hex `delivery_id`, `status` (completed,
failed, expired, late), `surface`, and `text`. After successful UI acceptance,
client sends `result.ack` with the same IDs. Duplicate IDs in the client's
64-entry in-memory cache are acknowledged again without render or speech.
Failed UI updates are not acknowledged. Backend retries unacknowledged retained
results at ten-second intervals, including reconnect, and preserves IDs across
backend restarts. Acknowledgement from one capable peer of the logical device
completes delivery; it does not prove every connected display presented it.
Cache loss/eviction can permit another presentation. Terminal records expire
at seven days or the global 10,000-row bound. See production-readiness.md for
the actual delivery guarantee and legacy-client compatibility.

### Read-only calendar scenes

Calendar cards reuse `timeline`, `artifact`, `message` and `ambient`. New accepted
actions: `calendar` / `calendar:N` (today/page), `calendar_next`,
`calendar_refresh`, `event:<16hex>` (device-scoped short-lived detail token).
They do not enqueue calendar writes. Progress uses existing `tool.activity`; scene
responses retain four cards/four buttons, 64-byte action buffers and a 30-second
return to ambient. Disconnected/revoked/error scenes contain no stale event cards;
transient failures may display explicitly stale cached cards within ten minutes.
There is no new firmware protocol opcode or OAuth endpoint on the LAN.
