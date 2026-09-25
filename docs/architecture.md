# Architecture

```text
P3A device / 720×720 simulator
  local touch, wake word, VAD, audio I/O
                 │
                 │ WebSocket: control JSON + PCM audio frames
                 ▼
FastAPI realtime gateway
  audio-turn buffer ─ STT adapter ─ session state ─ scene composer ─ tools
                         │               │
                         ▼               ▼
               local faster-whisper   Ollama / GPT / Claude
```

## Design boundaries

- The device never knows which model provider is active.
- Every visual is a temporary `surface`; there is no app launcher.
- Tools return structured data. The scene composer decides how to visualize it.
- Consequential tools will require confirmation. Mock read-only tools do not.
- Audio, STT, LLM, TTS, and tools are independent provider interfaces.
- VAD endpointing runs on the ESP32, and the future wake word belongs there as
  well. Raw audio streams only after an intentional activation.

## MVP sequence

1. Client sends `hello`; server sends ambient state and capabilities.
2. Client either sends a recognized text utterance or brackets binary PCM
   frames with `audio.start` and `audio.end`. The STT adapter emits the same
   text-utterance boundary used by typed input.
3. Server sends `thinking`, an optional transcript, tool activity, a dynamic surface, assistant text,
   and `speaking`/`idle` state transitions.
4. Reminder writes are committed to SQLite before success is announced.
