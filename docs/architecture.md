# Architecture

```text
P3A device / 720×720 simulator
  local touch, wake word, VAD, audio I/O
                 │
                 │ WebSocket: control JSON + PCM audio frames
                 ▼
FastAPI realtime gateway
  session state ─ scene composer ─ tool router
          │              │              │
          ▼              ▼              ▼
   GPT / Claude      UI protocol    calendar/reminders
          │
          ├── OpenAI Realtime (primary conversational voice)
          ├── streaming STT adapter (OpenAI first)
          └── WhisperFlow-compatible adapter (later fallback)
```

## Design boundaries

- The device never knows which model provider is active.
- Every visual is a temporary `surface`; there is no app launcher.
- Tools return structured data. The scene composer decides how to visualize it.
- Consequential tools will require confirmation. Mock read-only tools do not.
- Audio, STT, LLM, TTS, and tools are independent provider interfaces.
- Local wake word and VAD remain on the ESP32. Raw audio streams only after an
  intentional activation.

## MVP sequence

1. Client sends `hello`; server sends ambient state and capabilities.
2. Client sends a recognized text utterance.
3. Server sends `thinking`, tool activity, a dynamic surface, assistant text,
   and `speaking`/`idle` state transitions.
4. Reminder writes are committed to SQLite before success is announced.

