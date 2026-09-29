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
device. Its compile-time URL remains a recovery fallback.

On first boot, firmware creates a random 32-byte token and stores it in NVS.
The backend stores only its SHA-256 hash. An unpaired LAN device is accepted
only during the two-minute, loopback-opened pairing window:

```sh
curl --cacert data/tls/friday-ca.crt -X POST \
  -H "X-Friday-Admin-Token: $ASSISTANT_ADMIN_TOKEN" \
  -H 'X-Friday-Pairing: open' \
  https://127.0.0.1:8000/api/devices/pairing-window
```

The window closes as soon as one device enrolls. The stored identity is reused
after device and backend restarts. All WebSocket peers, including loopback
clients, must authenticate. The browser simulator uses a separate configured
credential rather than bypassing authentication.

Rotate an online physical identity with `X-Friday-Device-Action: rotate` at
`POST /api/devices/{device_id}/rotate`; this clears the device token, opens a
new one-device window, reboots, and enrolls a new token. `revoke` uses the same
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
treated as a rolling wake-word stream. The local backend uses faster-whisper to
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
{"type":"ready","protocol":1,"device_id":"friday-p3a","state":"idle","surface":{"kind":"ambient"}}
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
the server register the connection and send `ready`; firmware does not stream
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
after a natural affirmative follow-up. A negative response cancels them, and
an unrelated request supersedes them before being handled normally.

`reminder.alert` is sent only to peers advertising `reminder_ack`. The physical
device renders the alert for `expires_ms` and returns to idle without replying
when it expires. It sends `reminder.ack` only after the user taps the displayed
alert. Until that acknowledgement is stored, the backend retries delivery at a
bounded cadence and stops after the configured maximum number of attempts.

States are `idle`, `listening`, `thinking`, `speaking`, and `alert`. Surface
kinds currently implemented by the simulator are `ambient`, `timeline`,
`reminder`, and `message`.
