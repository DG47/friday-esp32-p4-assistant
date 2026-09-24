# Friday — ESP32-P4 personal assistant

Friday is a local-first personal-assistant stack for the Waveshare
ESP32-P4-WIFI6-Touch-LCD-4B (P3A). The ESP32 is the physical interface; the
backend owns conversation state, model routing, tools, reminders, and dynamic
scene generation.

## What works now

- 720×720 dynamic device simulator with ambient, listening, thinking, calendar,
  reminder, and speaking scenes
- WebSocket device protocol shared by the simulator and firmware
- deterministic mock agent for the complete acceptance flow
- SQLite-backed reminders that survive backend restarts
- mock calendar and contextual follow-up handling
- pluggable local Ollama and cloud GPT/Claude provider boundary
- ESP-IDF firmware for P3A with the native animated Friday scene, ST7703
  display initialization, GT911 touch input, and protocol state machine
- onboard ES7210 microphone capture at 24 kHz with live peak/RMS diagnostics
- first-boot Wi-Fi provisioning with host libraries matched to the factory C6
- physical-device WebSocket connection with automatic reconnect, touch
  activation, and server-driven states, surfaces, and Ollama responses
- synchronized browser and P3A display sessions using the `friday-p3a` device ID
- reproducible local ESP-IDF 5.5.5 toolchain and a verified ESP32-P4 build

The browser simulator uses the browser's speech recognition and speech
synthesis when available. Typed input always works. The physical microphones
are now verified; wake-word/VAD, audio streaming, and STT integration are the
next device-audio milestone. TTS playback still requires the external speaker.

## Run the MVP

1. For the already-installed local environment, run `make dev-local`. It listens
   on all local interfaces so the ESP32 can reach it over Wi-Fi. You can instead
   start Docker Desktop and run `make dev`.
2. Open <http://localhost:8000>.
3. Tap the orb and say or type: `What's next?`
4. Continue with: `With who?`
5. Then: `Remind me at noon to prepare.`

When the P3A is online, the browser and physical display share the same session;
browser text or speech drives the scenes shown on both displays.

The mock path needs no API keys. For fully local responses, install and start
Ollama, pull a model, and set these values in `.env`:

```env
ASSISTANT_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.6:latest
```

`make dev-local` uses the loopback URL above. Docker Compose automatically uses
`host.docker.internal` to reach Ollama on the Mac.

Cloud GPT and Claude adapters remain available by adding their keys locally and
choosing `openai`, `anthropic`, or `auto`. Never commit `.env`.

## Verify

Run `make test-local` (or `make test` with Docker). With the service running,
run `make smoke`. Run `make firmware-build` to reproduce the verified P4
firmware build using the repository-local toolchain.

## Hardware status

The connected serial device is expected to look like
`/dev/cu.usbmodem...`. Firmware setup instructions are in
[`firmware/README.md`](firmware/README.md). The native display/touch firmware
has been flashed and boot-verified on the connected ESP32-P4 revision 1.0. The
current board joined Wi-Fi successfully and its microphone capture was verified
with continuously changing, nonzero audio samples.

## Repository map

```text
backend/       FastAPI gateway, agent, tools, providers, and simulator
firmware/      ESP-IDF P3A scaffold and board configuration
docs/          architecture and device protocol
scripts/       local smoke checks
```
