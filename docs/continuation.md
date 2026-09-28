# Continuation context

This file is the durable handoff for future work on Friday. Update it whenever a
hardware milestone, protocol decision, or deployment assumption changes.

## Product goal

Build a personal voice assistant for the Waveshare
ESP32-P4-WIFI6-Touch-LCD-4B (P3A), without Ethernet. The experience is a
dynamic 720x720 scene rather than an app launcher. The intended path is local
wake word and VAD, streamed speech recognition, a GPT/Claude tool-using agent,
TTS, reminders, calendar integrations, and a dry, witty, competent butler
personality.

The architecture is deliberately provider-neutral. Audio, STT, LLM, TTS, and
tools should remain replaceable adapters. Basic VAD runs on the device. The
current exact-name detector streams idle audio to the trusted local backend;
moving wake-word detection on-device remains the privacy target.

## Verified state through 2026-09-28

Hardware:

- Board: Waveshare ESP32-P4-WIFI6-Touch-LCD-4B, hardware label P3A.
- Main SoC: ESP32-P4 revision 1.0; Wi-Fi coprocessor: factory ESP32-C6.
- Serial path on the development Mac: `/dev/cu.usbmodem5AE60376951`. Rediscover
  this before flashing because macOS device paths can change.
- Display: ST7703, 720x720, initialized through Waveshare BSP 3.0.1.
- Touch: GT911 at address `0x5D`; touch state cycling was verified physically.
- Microphones: onboard ES7210 capture is working at 24 kHz, signed 16-bit TDM,
  using FL+FR slots and 30 dB gain. Continuous changing PCM peak/RMS samples
  were observed after flashing. The UI displays live microphone levels.
- Speaker: none is attached. Use an MX1.25 2-pin, 8 ohm, 2 W speaker before
  enabling the ES8311/PA playback path.

Firmware and networking:

- ESP-IDF: v5.5.5, commit `b774170ff46c393eeb5e495ea37936038d3f4f4f`.
- `esp_hosted` is pinned to 2.9.0 and `esp_wifi_remote` to 1.2.5. These match the
  factory C6 hosted protocol and removed the previous version warning.
- Access-point provisioning and station mode were both verified. With no saved
  credentials the setup network is `Friday-Setup`, password `fridaysetup`, at
  `http://192.168.4.1`.
- Credentials are stored only in device NVS. They are not in this repository.
- At the final check, the board joined the LAN at `192.168.4.33`; this is a DHCP
  address and must not be treated as permanent.
- A local factory flash backup exists only in ignored `work/`; it is intentionally
  not uploaded because it may contain device-specific state.
- The physical firmware now connects to
  `ws://192.168.4.60:8000/ws/device/friday-p3a`, retries automatically, sends
  touch or wake-word activation, and renders backend state, surface, tool,
  error, and assistant-text messages. It reconnects after both transport
  failures and a clean backend shutdown. This was flashed and verified on the
  real board.
- Dynamic labels are re-anchored after every text change. This matters in LVGL:
  labels resize to their content and otherwise retain coordinates calculated
  for the previous size, which can place response text partly off-screen.

Backend and simulator:

- The FastAPI service binds to `0.0.0.0:8000` under `make dev-local`, allowing
  the ESP32 to reach it over the LAN. It remains available as
  `http://localhost:8000` on the Mac.
- At the final check, the Mac LAN address was `192.168.4.60` and the health
  endpoint was reachable there. This is also a DHCP address and may change.
- The browser simulator supports typed interaction plus browser speech APIs.
- The JSON WebSocket protocol is implemented at `/ws/device/{device_id}`.
  Multiple peers sharing an ID receive the same server messages; the browser
  and P3A use `friday-p3a` and were verified to stay synchronized.
- The configured model now drives a provider-neutral tool loop with bounded
  per-device conversation history. Calendar reads, reminder creation, and
  reminder listing are structured tools; mock mode retains its deterministic
  acceptance path. Ollama is the selected keyless provider on the development
  Mac, using `qwen3.6:latest` at `http://127.0.0.1:11434`. OpenAI and Anthropic
  use the same tool-loop contract.
- Personality instructions live in `backend/app/agent.py`.
- The personality now reads `ASSISTANT_USER_NAME` instead of hardcoding a user
  name. A live Ollama check selected `get_calendar`, consumed its result, and
  produced the expected grounded response and timeline surface.
- A background scheduler polls durable reminders, sends an `alert` state plus
  reminder surface and text to the originating device, and records delivery to
  prevent repeats. Due reminders remain pending while that device is offline,
  and existing databases migrate in place with device and delivery columns.
- Verification passed: 22 backend unit tests, the HTTP/WebSocket smoke test,
  ESP-IDF build, flash, boot, display, touch, Wi-Fi, and microphone capture.

Voice-loop implementation added on 2026-09-25:

- The firmware downmixes the verified 24 kHz FL+FR capture to mono, applies a
  simple low-pass/2:1 decimation, and streams 20 ms, 12 kHz signed 16-bit PCM
  frames between `audio.start` and `audio.end`.
- The first physical tap-to-talk → local Whisper → Ollama → display loop was
  confirmed by the user after the initial manual-endpointing build.
- The follow-up firmware adds adaptive on-device energy VAD. Tap once to start;
  60 ms of sustained speech arms endpointing, and 800 ms of trailing silence
  submits automatically. A second tap and a 12-second cutoff remain fallbacks.
  The VAD maintains an idle noise-floor estimate and derives speech/silence
  thresholds from configurable Kconfig ratios and minimums.
- The first VAD hardware attempt exposed two tuning issues: the observed speech
  level did not cross the original start threshold, and a server keepalive
  timeout closed the WebSocket during audio streaming. The speech ratio was
  reduced from 200% to 150% of ambient noise (with a 50 RMS floor), and the
  server ping timeout was raised to 60 seconds before the next physical test.
- That retest confirmed speech start and automatic 800 ms endpointing, but the
  raw microphone signal peaked near 145 RMS and Whisper returned no usable
  transcript. Streamed PCM now receives a saturating 8x digital boost while
  VAD continues to measure the unamplified signal.
- Sustained PCM streaming exposed transient hosted-Wi-Fi backpressure. The
  WebSocket client treated a frame that could not be written within 100 ms as
  a fatal transport error. Audio writes now allow up to one second for that
  congestion to clear. The component's separate transmit lock is also enabled
  so application frames and scheduled pings serialize safely, and the stream is
  decimated to 12 kHz to halve bandwidth while retaining speech-band content.
- The final physical retest completed without a transport drop: one tap led to
  VAD speech detection, automatic endpointing after 800 ms of silence,
  transcription, assistant response rendering, speaking, and return to idle.
- The backend buffers one bounded audio turn per WebSocket connection and
  routes it through a provider-neutral `SpeechTranscriber` interface.
- The default keyless adapter is faster-whisper `base.en`, loaded lazily on the
  Mac. The runtime and model download were verified. A synthesized speech turn
  produced the exact transcript `Give me a brief greeting.` and the local
  Ollama response `Good day, Dhruv. I am at your disposal.` through the live
  WebSocket service.
- The recognized transcript is broadcast to browser peers, then enters the
  same Friday/Ollama agent path as a typed utterance.
- While idle, the P3A batches its 12 kHz microphone stream to the local backend.
  A rolling faster-whisper probe detects the exact word `Friday` and tells the
  device to start the normal audio-turn path, with a short VAD guard so the
  keyword tail is not mistaken for the request. Touch remains a fallback.
- The exact-name build was flashed and verified on the physical P3A. Serial
  logs confirmed `wake.detected`, wake-word activation, speech start, automatic
  endpointing after 800 ms of silence, transcription, and the assistant path.
- Sixteen backend tests, the HTTP smoke test, and the ESP-IDF build pass. The new
  firmware was flashed to the physical P3A and serial logs confirmed boot,
  Wi-Fi at `192.168.4.33`, backend reconnection, and live microphone levels.
  The user subsequently confirmed the touch-triggered physical audio turn and
  on-display response. The automatic VAD build also completed a physical
  one-tap/pause turn through transcription, response rendering, and return to
  idle without a WebSocket drop.

## Not implemented yet

Do not confuse working microphone capture with a complete voice assistant. The
following remain:

1. Optimize wake-word latency or replace the rolling transcription probe with
   a dedicated on-device wake model if needed.
2. Replace mock calendar data with an authorized calendar integration.
3. Add a true streaming/cloud STT adapter if lower latency is needed.
4. Add TTS and interruption/barge-in after a speaker is attached.
5. Move the compile-time backend address into provisioning or local discovery.
6. Add authentication and transport security before using the service outside
   a trusted LAN.

## Resume commands

From the repository root:

```sh
make dev-local
make test-local
make smoke
make firmware-build
PORT=/dev/cu.usbmodem... make firmware-flash
PORT=/dev/cu.usbmodem... make firmware-monitor
```

The ESP-IDF checkout and tools live under ignored `work/`. On this Mac they are
already installed. A fresh clone needs ESP-IDF v5.5.5 plus its tools before
`scripts/idf.sh` can build. Never commit `.env`, databases, `firmware/sdkconfig`,
build outputs, toolchains, or factory backup images.

## Key files

- `README.md`: project overview and operator instructions.
- `docs/architecture.md`: architectural boundaries.
- `docs/device-protocol.md`: device/backend WebSocket protocol.
- `firmware/README.md`: hardware build, flash, and provisioning notes.
- `firmware/main/friday_audio.c`: ES7210 capture and live diagnostics.
- `backend/app/wake_session.py`: rolling idle-audio window and exact-word
  matching.
- `firmware/main/friday_vad.c`: adaptive energy VAD and endpointing state.
- `firmware/main/friday_wifi.c`: provisioning and station connection.
- `firmware/main/friday_ui.c`: native display scene.
- `backend/app/main.py`: FastAPI and WebSocket gateway.
- `backend/app/agent.py`: agent behavior, context, reminders, and personality.

## Product decisions already made

- Start with mock tools so the full UX can be tested without credentials. Use
  local Ollama for general responses without API charges; keep cloud providers
  optional.
- Prefer OpenAI Realtime or another true streaming STT service for latency.
  Wispr Flow, the desktop dictation product, is not a device dependency.
- Keep the UI scene-based and transient. Do not turn it into an app grid.
- Prioritize competence and brevity; sarcasm is seasoning, not the product.
- Avoid flashing the C6 independently unless a matching board-specific image
  has been obtained and the existing device state has been backed up.
