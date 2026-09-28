# Architecture

```text
P3A device / loopback 720×720 simulator
  local touch, wake word, VAD, audio I/O
                 │
                 │ UDP discovery, paired WebSocket control + PCM audio
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
- Consequential tools require a persisted confirmation before execution. Mock
  read-only tools do not.
- Audio, STT, LLM, TTS, and tools are independent provider interfaces.
- LAN hardware generates a persistent random identity in NVS and must enroll
  during a locally opened pairing window. The backend stores only the token
  hash. Device traffic uses `wss://` and validates a build-pinned Friday CA;
  the browser simulator has an independent explicit credential. Private CA and
  server keys remain outside Git.
- VAD endpointing runs on the ESP32. The current wake-word implementation sends
  idle microphone audio to the trusted local backend; moving exact `Friday`
  detection on-device remains the privacy target.

## MVP sequence

1. Firmware discovers the local backend (or uses its saved override), receives
   `hello.required`, and sends an authenticated `hello`. The server registers
   it only after the saved identity verifies, then sends `ready` and the
   ambient state. Loopback simulator clients use their separate explicit
   credential instead of hardware pairing.
2. Client either sends a recognized text utterance or brackets binary PCM
   frames with `audio.start` and `audio.end`. The STT adapter emits the same
   text-utterance boundary used by typed input.
3. The gateway persists the request as a job and immediately returns
   `job.accepted`; model and tool work no longer blocks the device socket.
4. A bounded worker pool runs different devices in parallel while SQLite
   claims serialize turns for the same device. Each turn uses durable structured
   conversation history. Expired work is recovered, failed attempts back off,
   stale results are discarded, and current results survive disconnects.
   Consequential tool calls pause on a restart-safe confirmation record.
5. Server sends tool activity, a dynamic surface, assistant text, and
   `speaking`/`idle` state transitions. A reconnect triggers pending-result
   delivery.
6. Reminder writes and state transitions are committed idempotently before
   success is announced. Cancel, complete, and snooze actions are device-scoped,
   require confirmation, and operate on stable reminder IDs. Ambiguous mock-mode
   matches stop for clarification. Snoozing resets delivery state atomically.
7. The background scheduler claims due reminders, sends `reminder.alert` only
   to capable firmware, and retries at a bounded cadence. Firmware honors the
   alert expiry without acknowledging it; delivery is recorded only when a
   user tap produces `reminder.ack`.
