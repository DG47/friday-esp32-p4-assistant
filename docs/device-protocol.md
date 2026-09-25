# Device protocol v1

Transport: WebSocket at `/ws/device/{device_id}`. Control messages are UTF-8
JSON. PCM audio frames are binary WebSocket messages associated with the active
turn on that same connection.

Multiple clients may join the same `device_id`. Server messages are broadcast
to every connected peer for that device, allowing the browser input/debug view
and the physical display to stay synchronized. The default physical device ID
is `friday-p3a`.

## Client messages

```json
{"type":"hello","protocol":1,"capabilities":["display","touch"]}
{"type":"activate","source":"touch"}
{"type":"utterance","text":"What's next?"}
{"type":"audio.start","format":"pcm_s16le","sample_rate":24000,"channels":1}
{"type":"audio.end"}
```

For a voice turn, send `audio.start`, one or more binary PCM frames, then
`audio.end`. The current device sends mono signed 16-bit little-endian PCM at
24 kHz in 20 ms frames. The backend also accepts mono 16 kHz PCM. Only one
audio turn may be active per connection; turns shorter than 200 ms or longer
than `STT_MAX_SECONDS` are rejected. Binary data outside a turn is rejected.

The P3A interaction is tap once to start and tap again to submit, with an
automatic 12-second cutoff. This capture boundary is intentionally separate
from STT so a later WakeNet9/VAD path can use the same wire protocol.

## Server messages

```json
{"type":"state","state":"thinking"}
{"type":"transcript","text":"What's next?","provider":"faster-whisper"}
{"type":"assistant.text","text":"Your interview is at two."}
{"type":"surface","surface":{"kind":"timeline","title":"Today","items":[]}}
{"type":"tool.activity","name":"calendar","status":"working"}
{"type":"error","code":"bad_message","message":"..."}
```

States are `idle`, `listening`, `thinking`, `speaking`, and `alert`. Surface
kinds currently implemented by the simulator are `ambient`, `timeline`,
`reminder`, and `message`.
