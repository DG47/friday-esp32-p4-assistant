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
- model-driven tool loop for Ollama, GPT, and Claude, with bounded per-device
  user, assistant, tool-call, and tool-result history persisted in SQLite
- durable SQLite-backed agent jobs with immediate acceptance, bounded execution,
  retry/backoff, expired-lease recovery, stale-result suppression, and result
  delivery after reconnection
- bounded cross-device worker concurrency with strict per-device turn ordering
- restart-safe confirmation before reminder creation and other consequential
  tool actions
- SQLite-backed reminders that survive backend restarts
- idempotent reminder creation keyed to the originating request
- proactive reminder delivery that retries through disconnects and records
  delivery only after an acknowledgement from reminder-capable firmware
- mock calendar data exposed through the same tool interface intended for a
  future real calendar adapter
- pluggable local Ollama and cloud GPT/Claude provider boundary
- ESP-IDF firmware for P3A with the native animated Friday scene, ST7703
  display initialization, GT911 touch input, and protocol state machine
- onboard ES7210 microphone capture at 24 kHz with live peak/RMS diagnostics
- tap-to-talk 12 kHz PCM streaming from the P3A's 24 kHz capture path to the
  backend, with a 12-second recording safety limit
- adaptive on-device energy VAD that submits a turn after 800 ms of trailing
  silence; a second tap remains available as a manual fallback
- local-backend `Friday` wake detection using faster-whisper, with touch
  retained as a fallback
- WebSocket audio writes tolerate brief hosted-Wi-Fi backpressure and serialize
  with client keepalive pings
- provider-neutral speech transcription with local faster-whisper `base.en`
  as the default keyless adapter
- first-boot Wi-Fi provisioning with host libraries matched to the factory C6
- local UDP backend discovery with an optional setup-page WebSocket override
- persistent per-device pairing: Friday generates its identity on-device,
  while the backend stores only a hash and requires an explicit local pairing
  window for first enrollment
- CA-pinned `wss://` transport verified against both the real backend and a
  rejected server presenting an untrusted certificate
- physical-device WebSocket connection with automatic reconnect, touch
  activation, and server-driven states, surfaces, and Ollama responses
- synchronized browser and P3A display sessions using the `friday-p3a` device ID
- reproducible local ESP-IDF 5.5.5 toolchain and a verified ESP32-P4 build

The browser simulator uses the browser's speech recognition and speech
synthesis when available. Typed input always works. On the P3A, say `Friday`,
pause briefly, then speak; Friday submits after you pause. Tapping
the orb remains available as a fallback or to force an early submission. TTS
playback still requires the external speaker.

## Run the MVP

1. Generate the local Friday CA/server certificate the first time, using the
   Mac's current LAN address, then build/flash firmware so it contains that CA:

   ```sh
   make tls-local LAN_IP=192.168.4.38
   make firmware-build
   PORT=/dev/cu.usbmodem... make firmware-flash
   ```

   The private CA and server keys remain under ignored `data/tls/`; only the
   public CA certificate is committed for firmware verification.
2. Add independent 64-hex-character `ASSISTANT_ADMIN_TOKEN` and
   `ASSISTANT_SIMULATOR_TOKEN` values to `.env` (`openssl rand -hex 32`), then
   run `make dev-local`. It listens with HTTPS/WSS on all local interfaces so
   the ESP32 can reach it over Wi-Fi. You can instead run `make dev`.
   Export the same admin value in the shell running the management request.
   On a device's first authenticated connection, open a two-minute pairing
   window from the same Mac:

   ```sh
   curl --cacert data/tls/friday-ca.crt -X POST \
     -H "X-Friday-Admin-Token: $ASSISTANT_ADMIN_TOKEN" \
     -H 'X-Friday-Pairing: open' \
     https://127.0.0.1:8000/api/devices/pairing-window
   ```

   Then boot or reconnect Friday. The window closes after the first device
   enrolls. Later reconnects and backend restarts use the identity saved in the
   device and do not require another pairing window.
3. Open <https://localhost:8000>, accept/trust the private Friday CA locally,
   and paste `ASSISTANT_SIMULATOR_TOKEN` into the simulator credential field.
4. Tap the orb and say or type: `What's next?`
5. Continue with: `With who?`
6. Then: `Remind me at noon to prepare.` Friday asks for confirmation; answer
   `yes` to create it or `no` to cancel it.

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

Local device speech uses faster-whisper and downloads the `base.en` model on
the first voice turn. Its settings are `STT_PROVIDER`, `STT_MODEL`,
`STT_DEVICE`, `STT_COMPUTE_TYPE`, `STT_LANGUAGE`, and `STT_MAX_SECONDS`.
Set `STT_PROVIDER=none` to disable speech transcription without affecting typed
input. The STT interface is independent of the agent provider so another local
or cloud transcriber can be added without changing the device protocol.

Cloud GPT and Claude adapters remain available by adding their keys locally and
choosing `openai`, `anthropic`, or `auto`. Never commit `.env`.

The reminder scheduler checks for due work once per second by default. Override
this with `ASSISTANT_REMINDER_POLL_SECONDS` when needed. An alert remains on
the device for 60 seconds and is acknowledged only when the user taps it. An
unacknowledged reminder retries every 90 seconds by default, up to 10 attempts;
configure those bounds with `ASSISTANT_REMINDER_RETRY_SECONDS` and
`ASSISTANT_REMINDER_MAX_ATTEMPTS`.

Agent turns are accepted before model work begins and run from a durable local
queue. `ASSISTANT_JOB_TIMEOUT_SECONDS` bounds each attempt. Interrupted leases
are recovered after restart, transient failures retry with backoff, and a
completed response remains pending until a matching device reconnects.
`ASSISTANT_JOB_MAX_AGE_SECONDS` prevents an old queued or offline result from
being presented as current. `ASSISTANT_JOB_CONCURRENCY` controls cross-device
parallelism while SQLite claims guarantee that one device's turns remain
serial. Consequential tool confirmations expire after
`ASSISTANT_CONFIRMATION_TIMEOUT_SECONDS` and survive backend restarts.

## Production-readiness boundary

This is a hardened local prototype, not yet an internet-facing production
service. The durable job/reminder paths now cover process restart, retry,
bounded concurrency, stale-result handling, confirmation recovery, and device
disconnect scenarios. LAN clients require explicit credentials, and the
physical path now uses a device-pinned local CA over `wss://`; a physically
tested rogue certificate was rejected. Production deployment still requires a
managed certificate/secret lifecycle, stronger key storage/NVS encryption,
operational
metrics/alerting, backup and retention policy, load/failure testing, and live
validation of any selected cloud providers. Agent results also need a
device-level acknowledgement if exactly-once presentation is required;
reminder alerts already have one.

## Verify

Run `make test-local` (or `make test` with Docker). With the service running,
run `make smoke`. Run `make firmware-build` to reproduce the verified P4
firmware build using the repository-local toolchain. A fresh local environment
must first install `backend/requirements.txt` into `.venv`.

## Hardware status

The connected serial device is expected to look like
`/dev/cu.usbmodem...`. Firmware setup instructions are in
[`firmware/README.md`](firmware/README.md). The native display/touch firmware
has been flashed and boot-verified on the connected ESP32-P4 revision 1.0. The
board has joined Wi-Fi successfully and its microphone capture was verified
with continuously changing, nonzero audio samples. The reminder-alert expiry
and retry path was also verified on the physical P3A: the device rendered an
alert, returned to idle after 60 seconds without falsely acknowledging it, and
rendered the bounded 90-second retry. A later physical tap sent
`reminder.ack`, returned the UI to idle, and recorded delivery in SQLite. The
tap-to-talk → local
Whisper → Ollama → display loop was confirmed by the user on the physical P3A.
The automatic VAD endpoint was also confirmed on the physical P3A through a
complete one-tap voice turn without a WebSocket drop. LAN discovery, first
pairing, identity persistence across a firmware reboot, and authenticated
reconnection after a backend restart were also verified on the physical board.
The TLS build connected successfully, rejected an untrusted replacement server,
recovered when the real backend returned, and completed an end-to-end device
identity rotation and re-enrollment.

## Repository map

```text
backend/       FastAPI gateway, agent, tools, providers, and simulator
firmware/      ESP-IDF P3A scaffold and board configuration
docs/          architecture and device protocol
scripts/       local smoke checks
```
