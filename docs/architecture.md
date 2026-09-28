# Architecture

```text
P3A device / 720×720 simulator
  local touch, wake word, VAD, audio I/O
                 │
                 │ WebSocket: control JSON + PCM audio frames
                 ▼
FastAPI realtime gateway
  audio-turn buffer ─ STT adapter ─ tool-calling agent ─ scene composer
                         │               │       │
                         ▼               ▼       ▼
               local faster-whisper   history  tools
                                         │
                                         ▼
                                Ollama / GPT / Claude
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
3. The selected model receives bounded conversation history and structured
   tool definitions. It may call calendar or reminder tools before composing
   the final response.
4. Server sends `thinking`, an optional transcript, tool activity, a dynamic
   surface, assistant text, and `speaking`/`idle` state transitions.
5. Reminder writes are committed to SQLite before success is announced.
