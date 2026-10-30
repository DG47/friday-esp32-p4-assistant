# Friday

[![Friday regression tests](https://github.com/DG47/friday-esp32-p4-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/DG47/friday-esp32-p4-assistant/actions/workflows/tests.yml)

Say "Friday", pause, and ask what is next. A 4-inch touch screen on the desk
shows the answer, and a reminder you set from it still fires after the backend
has been restarted. That is the whole idea: a voice assistant
that lives on a Waveshare ESP32-P4 board and a backend on the Mac next to it,
with nothing leaving the LAN unless you turn a cloud provider on.

The board is the face and the ears. The backend owns the conversation, the
tools, the reminders and the model routing, and the same backend drives a
browser simulator so you can work on all of it without hardware.

## What it does

- Listens for the wake word, records until you pause, transcribes locally with
  faster-whisper, answers with a local Ollama model by default.
- Reminders and timers that are stored in SQLite and delivered to the screen,
  with retries through disconnects and acknowledgement by touch. Cancelling or
  completing one asks for confirmation; snoozing and creating do not.
- Status and reminder scenes rendered natively on the board with LVGL and
  mirrored in the browser. Calendar and weather scenes exist behind a firmware
  option that is off by default; they are verified on the host renderer, not
  yet on the panel.
- Read-only Google Calendar, two-location weather, and Spotify playback
  controls with timed lyrics, each off until you connect it.
- A device protocol over `wss://` with a device-pinned local CA, on-device
  identity, and an explicit pairing window for first enrollment.

## What it does not do yet

- Speak. The onboard microphone works; the external speaker, device TTS and
  barge-in are not wired up, so answers are read on the screen.
- Hear the wake word on the board. Detection runs on the backend; the firmware
  gates uploads with an energy VAD.
- Run on the internet. It is built for one LAN, and the acceptance notes say
  so in detail: [production acceptance](docs/production-acceptance-current.md).

Echo cancellation on the board is an experiment that is off by default. The
Telegram connector and the Mac file reports exist behind flags and are
documented, but they are not part of the default build.

## Run it without hardware

You need Docker, or Python 3.12 with a `.venv` holding
`backend/requirements.txt`. For local answers you also need
[Ollama](https://ollama.com).

1. Copy `.env.example` to `.env`. Generate the two tokens it asks for with
   `openssl rand -hex 32` and set `ASSISTANT_ADMIN_TOKEN` and
   `ASSISTANT_SIMULATOR_TOKEN`.
2. `make tls-local` creates the local CA and a server certificate for your
   Mac's LAN address (pass `LAN_IP=...` if it guesses wrong). The backend only
   speaks HTTPS, even to the browser. This also rewrites the committed
   `firmware/main/friday_backend_ca.pem` with your CA, which is what the
   firmware pins.
3. `make dev` (Docker) or `make dev-local` (the `.venv`). Both listen on
   port 8000, so if this Mac already runs Friday as a LaunchAgent, boot that
   out first ([macOS operations](docs/macos-operations.md)). The mock provider
   needs no API key. For real answers, start Ollama, pull a model, and set:

   ```env
   ASSISTANT_PROVIDER=ollama
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   OLLAMA_MODEL=qwen3.6:latest
   ```

4. Open <https://localhost:8000>, trust the local Friday CA, and paste
   `ASSISTANT_SIMULATOR_TOKEN` into the credential field.
5. Tap the orb and type or say `What's next?`, then `Remind me at noon to
   prepare.`, then `Snooze the prepare reminder for 20 minutes`.

The browser uses its own speech recognition and synthesis when available.
Typed input always works.

## Run it on the board

The target is the Waveshare ESP32-P4-WIFI6-Touch-LCD-4B, a 720×720 display
with GT911 touch, an ES7210 microphone and an ESP32-C6 for Wi-Fi. Toolchain
setup and board bring-up are in [`firmware/README.md`](firmware/README.md).

1. With the CA from `make tls-local` in place, build and flash so the
   firmware carries it:

   ```sh
   make firmware-build
   PORT=/dev/cu.usbmodem... make firmware-flash
   ```

   The private keys stay under the ignored `data/tls/`; only the public CA
   certificate is committed.
2. Run `make dev-local` so the backend listens on all interfaces over HTTPS.
3. On first boot the board opens a `Friday-Setup` network for Wi-Fi
   provisioning, then finds the backend over UDP. When it shows a pairing code,
   open a two-minute pairing window from the Mac:

   ```sh
   curl --cacert data/tls/friday-ca.crt -X POST \
     -H "X-Friday-Admin-Token: $ASSISTANT_ADMIN_TOKEN" \
     -H 'X-Friday-Pairing: open' \
     -H 'X-Friday-Device-ID: friday-p3a' \
     -H 'X-Friday-Pairing-Code: ABCD-1234' \
     https://127.0.0.1:8000/api/devices/pairing-window
   ```

   The window closes after the first device enrolls. Later reconnects and
   backend restarts reuse the identity saved on the device.

After that the browser and the board share one session. Say `Friday`, pause,
and speak; a tap on the orb submits early or works as a fallback.

What has been checked on a physical board: display and touch, Wi-Fi
provisioning, microphone capture, the full reminder lifecycle including a
snooze and a retry, the tap-to-talk and automatic-endpoint voice turns through
Whisper and Ollama, LAN discovery and first pairing, reconnection after a
backend restart, a rejected rogue certificate, and a device identity rotation.
What has not: the speaker path, echo cancellation, and any long soak.

## Configuration

Providers are `mock`, `ollama`, `openai`, `anthropic` or `auto`; with both
cloud keys present, `ASSISTANT_AUTO_PROVIDER_PREFERENCE` picks one instead of
guessing. Speech recognition is `STT_PROVIDER=faster-whisper` with `base.en`
by default and `none` to disable it. Reminder polling, retry, job timeout,
concurrency and confirmation timeout all have `ASSISTANT_*` variables, and
`.env.example` lists every one with its default. Never commit `.env`.

Optional integrations each have a setup document:
[Google Calendar](docs/google-calendar.md),
[weather](docs/weather.md),
[Spotify runtime](docs/spotify-runtime.md) and
[authentication](docs/spotify-auth.md),
[local Mac speech](docs/local-voice.md),
[explicit memory](docs/explicit-memory.md),
[Telegram reports](docs/report-telegram-setup.md).

## Verify

`make test-local` runs the backend suite without providers, or `make test`
does it in Docker. With the service up, `ASSISTANT_READ_TOKEN="$ASSISTANT_SIMULATOR_TOKEN" make smoke`
exercises it end to end. `make firmware-build` reproduces the firmware build
with the repository-local ESP-IDF 5.5.5 toolchain.

## Repository map

```text
backend/     FastAPI gateway, agent loop, tools, providers, simulator
firmware/    ESP-IDF project for the P4 board
docs/        architecture, device protocol, integration setup
docs/notes/  dated engineering notes and acceptance audits
scripts/     TLS setup, smoke checks, calendar and Spotify connectors
```

Start with [architecture](docs/architecture.md) and the
[device protocol](docs/device-protocol.md). The roadmap and open items are in
[TODO.md](TODO.md).

## License

MIT. See [LICENSE](LICENSE).
