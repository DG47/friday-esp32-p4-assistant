# Device protocol v1

Transport: WebSocket at `/ws/device/{device_id}`. Control messages are UTF-8
JSON. PCM audio frames are binary WebSocket messages associated with either
idle wake-word detection or the active turn on that same connection.

Multiple clients may join the same `device_id`. Server messages are broadcast
to every connected peer for that device, allowing the browser input/debug view
and the physical display to stay synchronized. The default physical device ID
is `friday-p3a`.

## Client messages

```json
{"type":"hello","protocol":1,"capabilities":["display","touch"]}
{"type":"wake.start","format":"pcm_s16le","sample_rate":12000,"channels":1}
{"type":"activate","source":"touch"}
{"type":"activate","source":"wake_word"}
{"type":"utterance","text":"What's next?"}
{"type":"audio.start","format":"pcm_s16le","sample_rate":12000,"channels":1}
{"type":"audio.end"}
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
{"type":"wake.detected","wake_word":"Friday"}
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
