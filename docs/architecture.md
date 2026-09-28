# Architecture

```text
P3A device / 720×720 simulator
  local touch, wake word, VAD, audio I/O
                 │
                 │ WebSocket: control JSON + PCM audio frames
                 ▼
FastAPI realtime gateway
  audio-turn buffer ─ STT adapter ─ durable agent queue ─ tool-calling agent
                         │                 │                  │       │
                         ▼                 ▼                  ▼       ▼
               local faster-whisper   lease/retry      SQLite history/tools
                                                              │
                                                              ▼
                                                    Ollama / GPT / Claude
                                 reminder scheduler ──► alert ──► device ack
```

## Design boundaries

- The device never knows which model provider is active.
- Every visual is a temporary `surface`; there is no app launcher.
- Tools return structured data. The scene composer decides how to visualize it.
- Consequential tools will require confirmation. Mock read-only tools do not.
- Audio, STT, LLM, TTS, and tools are independent provider interfaces.
- VAD endpointing runs on the ESP32. The current wake-word implementation sends
  idle microphone audio to the trusted local backend; moving exact `Friday`
  detection on-device remains the privacy target.

## MVP sequence

1. Client sends `hello`; server sends ambient state and capabilities.
2. Client either sends a recognized text utterance or brackets binary PCM
   frames with `audio.start` and `audio.end`. The STT adapter emits the same
   text-utterance boundary used by typed input.
3. The gateway persists the request as a job and immediately returns
   `job.accepted`; model and tool work no longer blocks the device socket.
4. A leased worker runs the selected model with bounded SQLite conversation
   history and structured tool definitions. Expired work is recovered, failed
   attempts back off, and completed results survive device disconnects.
5. Server sends tool activity, a dynamic surface, assistant text, and
   `speaking`/`idle` state transitions. A reconnect triggers pending-result
   delivery.
6. Reminder writes are committed idempotently before success is announced.
7. The background scheduler claims due reminders, sends `reminder.alert` only
   to capable firmware, retries unacknowledged delivery, and records delivery
   only after `reminder.ack`.
