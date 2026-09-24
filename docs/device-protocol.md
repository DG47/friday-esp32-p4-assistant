# Device protocol v1

Transport: WebSocket at `/ws/device/{device_id}`. JSON messages are UTF-8.
Future PCM audio frames use binary WebSocket messages and are associated with
the current turn.

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

## Server messages

```json
{"type":"state","state":"thinking"}
{"type":"assistant.text","text":"Your interview is at two."}
{"type":"surface","surface":{"kind":"timeline","title":"Today","items":[]}}
{"type":"tool.activity","name":"calendar","status":"working"}
{"type":"error","code":"bad_message","message":"..."}
```

States are `idle`, `listening`, `thinking`, `speaking`, and `alert`. Surface
kinds currently implemented by the simulator are `ambient`, `timeline`,
`reminder`, and `message`.
