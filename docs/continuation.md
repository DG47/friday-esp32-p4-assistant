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
tools should remain replaceable adapters. Wake-word detection and basic VAD
belong on the device. Raw audio should leave it only after activation.

## Verified state on 2026-09-24

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

Backend and simulator:

- The FastAPI service binds to `0.0.0.0:8000` under `make dev-local`, allowing
  the ESP32 to reach it over the LAN. It remains available as
  `http://localhost:8000` on the Mac.
- At the final check, the Mac LAN address was `192.168.4.60` and the health
  endpoint was reachable there. This is also a DHCP address and may change.
- The browser simulator supports typed interaction plus browser speech APIs.
- The JSON WebSocket protocol is implemented at `/ws/device/{device_id}`.
- Mock calendar, contextual follow-ups, and SQLite reminders work. OpenAI and
  Anthropic text adapters exist and activate only when local API keys are set.
- Personality instructions live in `backend/app/agent.py`.
- Verification passed: three backend unit tests, the HTTP/WebSocket smoke test,
  ESP-IDF build, flash, boot, display, touch, Wi-Fi, and microphone capture.

## Not implemented yet

Do not confuse working microphone capture with a complete voice assistant. The
following remain:

1. Connect the physical device to the backend WebSocket.
2. Stream captured PCM frames after activation.
3. Add local WakeNet9 wake-word detection and VAD/endpointing.
4. Add streaming STT, initially OpenAI Realtime/transcription; keep a
   WhisperFlow-compatible or local Whisper adapter as a fallback.
5. Render server-generated surfaces and conversation states on the physical
   display rather than only the local touch demo.
6. Add TTS and interruption/barge-in after a speaker is attached.
7. Replace mock calendar data with an authorized calendar integration.
8. Add reminder scheduling/notifications rather than storage alone.
9. Add authentication and transport security before using the service outside
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
- `firmware/main/friday_wifi.c`: provisioning and station connection.
- `firmware/main/friday_ui.c`: native display scene.
- `backend/app/main.py`: FastAPI and WebSocket gateway.
- `backend/app/agent.py`: agent behavior, context, reminders, and personality.

## Product decisions already made

- Start with mock tools and providers so the full UX can be tested without API
  keys; choose real providers automatically when keys are present.
- Prefer OpenAI Realtime or another true streaming STT service for latency.
  Wispr Flow, the desktop dictation product, is not a device dependency.
- Keep the UI scene-based and transient. Do not turn it into an app grid.
- Prioritize competence and brevity; sarcasm is seasoning, not the product.
- Avoid flashing the C6 independently unless a matching board-specific image
  has been obtained and the existing device state has been backed up.
