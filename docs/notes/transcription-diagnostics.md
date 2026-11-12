# Bounded transcription diagnostics candidate

Base: `466f0055afb5af10d4d2836999a7a5248bb14c69`. Implementation/testing only;
this candidate is not deployed and does not fix an established physical STT cause.

On a failed `audio.end`, one additional `transcription_diag` record logs a fixed
reason, fixed phase, input byte count, validated rate/channels, derived audio
milliseconds, and monotonic total/model-load/input-preparation/decode microseconds.
It does not log device IDs, PCM, recognized text, exception text, model paths,
credentials or arbitrary labels. Existing generic `device_error` records and
client error messages are preserved. Successful turns add no log record.

Reasons: `capture_missing`, `capture_malformed`, `input_short`, `stt_disabled`,
`model_load`, `input_prepare`, `decode`, `timeout`, `empty`, and `stt_error` for
unclassified errors. Classification uses validated capture state and typed provider
failure metadata; it never parses exception strings. Reasons from arbitrary
transcribers are accepted only when they are members of the fixed enum.

`capture_missing` means no active capture at `audio.end`; it does not distinguish
an omitted start from an earlier reset after oversized input. `capture_malformed`
refers to the complete-sample validation in `AudioSession.finish`; normal socket
framing already rejects odd-byte packets. `audio_ms` is derived from received
sample bytes, not capture wall time or proof of sample continuity.

Timing definitions:

* `total_us` starts at `audio.end` handling, not microphone capture, and includes
  state-broadcast and scheduling time before STT.
* `model_load_us` includes the model cache check, loading lock wait, import and
  lazy construction. It is not a cold-load-only benchmark.
* `input_prepare_us` covers existing temporary WAV creation/write/close.
* `decode_us` includes `model.transcribe` and complete consumption of lazy segments
  (including VAD), not merely construction of its iterator.
* On timeout, `phase` identifies the observed active phase or executor wait;
  timing includes the active phase's partial duration at the snapshot. Times
  saturate at 2,147,483,647 microseconds.

A ContextVar binds one observer per turn, copied to the existing executor thread.
A short mutex guards phase/duration writes and snapshots. No lock spans an await,
file write, model call, logging call or segment iteration. After cancellation a
worker may continue, as before, but can update only its original observer; it
never logs and cannot mutate another turn. No queue, model cache, cancellation,
recognition parameters, 60-second deadline, client packets or job flow changed.
The pre-existing model/decoder exception message wrapping is retained; unexpected
executor/cleanup exceptions use the unclassified reason rather than claiming a
model-load cause. Wake probes add no new diagnostic record.

Validation uses only synthetic byte buffers and fake models/transcribers: missing,
short/malformed, disabled, model-load/prepare/decode, empty and actual short test
deadline failures; unchanged client messages; no enqueue on failure; same-socket
next-turn recovery; lazy iterator failures; numeric timing and partial-duration
accounting; RuntimeError argument compatibility; malicious exception/label privacy;
late executor isolation. No real model is constructed and no provider inference
or real audio capture occurs. Aggregate backend and synthetic localhost callback
regressions also run in the isolated checkout. Firmware source is unchanged.

Limitations: existing historical failures remain unclassified. Actual model health,
physical capture quality, echo cancellation and end-to-end voice success remain
unverified. Diagnostics authentication and live runtime/configuration are untouched.
A later separately approved installation and bounded failure observation would be
needed to diagnose the live failures.

Final local validation: 14 targeted diagnostic tests, 327 aggregate backend tests,
10 synthetic localhost callback scenarios, Python compileall and git whitespace
checks passed. A 10,000-iteration host-only observer/three-phase/snapshot benchmark
averaged 5.9 microseconds per iteration; this excludes logging, file work and model
execution and is not a loaded-runtime latency guarantee.
