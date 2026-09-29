# P3A firmware

This is the ESP-IDF firmware for the Waveshare
ESP32-P4-WIFI6-Touch-LCD-4B/P3A. It uses Waveshare's published 3.0.1 BSP for
the ST7703 MIPI display, GT911 touch controller, and board pin definitions.
The current hardware milestone renders Friday's native animated state scene,
supports touch interaction, provides first-boot Wi-Fi provisioning, captures
live audio from the board's ES7210 microphone array, detects `Friday` through
the local backend, and streams voice turns for transcription and response.

## Install the macOS toolchain

The repository-local ESP-IDF 5.5.5 toolchain is installed under ignored
`work/` directories, without modifying shell startup files. Waveshare supports
5.3.1 or later and demonstrates 5.5.2; this project stays on the 5.5 line.

Build through the wrapper from the repository root:

```sh
make firmware-build
```

The verified build produces `firmware/build/friday_p3a.bin`. It does not flash
the connected device.

The currently detected serial path is likely `/dev/cu.usbmodem5AE60376951`.
Confirm it again before flashing:

```sh
ls /dev/cu.usbmodem*
PORT=/dev/cu.usbmodem5AE60376951 make firmware-flash
PORT=/dev/cu.usbmodem5AE60376951 make firmware-monitor
```

## Bring-up status

1. Done: ST7703 display initialized at 720×720 through the official BSP.
2. Done: GT911 detected at `0x5D` and registered as an LVGL input device.
3. Done: native idle/listening/thinking/speaking/alert scene and touch cycling.
4. Done: hosted Wi-Fi and a first-boot setup portal using `esp_wifi_remote`
   1.2.5 and `esp_hosted` 2.9.0, matched to the factory ESP32-C6 firmware.
5. Done: backend WebSocket connection with automatic reconnect, touch
   activation, and JSON-driven states/surfaces on the LCD.
6. Done: ES7210 microphone capture at 24 kHz, with live peak/RMS levels on
   screen and in the serial log.
7. Done: tap-to-talk captures at 24 kHz and streams decimated 12 kHz mono PCM
   over the existing WebSocket to avoid hosted Wi-Fi backpressure. Adaptive
   energy VAD submits after 800 ms of trailing silence; tap again to force
   submission, or recording stops automatically after 12 seconds.
8. Done: the streaming build was flashed and booted on the physical P3A; Wi-Fi,
   backend reconnection, and microphone capture were confirmed in serial logs.
9. Done: the user confirmed a touch-triggered physical transcription and
   on-display response with the manual endpointing build.
10. Done: the automatic VAD build completed a physical one-tap voice turn from
    speech detection through transcription, response rendering, and return to
    idle without dropping the WebSocket.
11. Done: while idle, the device streams batched 12 kHz audio to the local
    faster-whisper backend for exact `Friday` detection. A physical test
    completed wake detection, speech capture, automatic endpointing,
    transcription, and the assistant path. Touch remains a fallback.
12. Done: the physical P3A received and rendered `reminder.alert`, left it
    pending when untouched, returned to idle after its 60-second expiry, and
    rendered the backend's 90-second retry. A tap is the only action that sends
    `reminder.ack`; that tap and the resulting persisted acknowledgement were
    verified on the physical device.
13. Done: the firmware discovered the backend over UDP, enrolled once during a
    locally opened pairing window, reused its NVS identity after reflashing,
    and authenticated again after a live backend restart with pairing closed.
14. Done: the physical firmware connected over CA-pinned `wss://`, rejected a
    replacement server with an untrusted certificate, recovered when the real
    backend returned, and rotated/re-enrolled its device identity.
15. Attach an MX1.25 2-pin 8Ω/2W speaker before enabling ES8311 playback.

## First-boot Wi-Fi setup

With no saved credentials, the device creates this temporary setup network:

- Network: `Friday-Setup`
- Password: `fridaysetup`
- Setup page: `http://192.168.4.1`

Submit the local Wi-Fi name and password in the setup page. Leave the optional
Backend WebSocket URL blank to use local discovery; enter a `wss://` URL only
when a fixed override is required. Plain `ws://` is rejected. The device stores
the settings in its NVS partition, restarts, and switches to station mode. NVS encryption is
not enabled in this MVP. After five failed connection attempts, the device
clears the saved network settings and returns to setup mode.

For a device that has not previously paired with this backend, start the
backend, open a two-minute enrollment window locally, and boot/reconnect Friday:

```sh
curl --cacert data/tls/friday-ca.crt -X POST \
  -H "X-Friday-Admin-Token: $ASSISTANT_ADMIN_TOKEN" \
  -H 'X-Friday-Pairing: open' \
  -H 'X-Friday-Device-ID: friday-p3a' \
  https://127.0.0.1:8000/api/devices/pairing-window
```

Friday generates its 32-byte device token on-device and never writes it to the
log. The backend retains only a hash. Pairing closes after the first enrollment
and normal reconnects require no operator action. The server must present a
certificate signed by the Friday CA embedded in the firmware.

The P4 host libraries are deliberately pinned to `esp_hosted` 2.9.0 and
`esp_wifi_remote` 1.2.5 to match the factory ESP32-C6 hosted firmware. This
removes the host/coprocessor protocol warning and has been verified in both
access-point and station modes. Do not upgrade or flash the C6 independently
without obtaining the matching board-specific image.

The browser simulator is the source of truth for interaction behavior; the
firmware renderer consumes the same scene/state protocol.

On each boot the firmware waits for Wi-Fi, then chooses the backend in this
order: last authenticated/setup-page endpoint, UDP discovery on port 8334,
compile-time `FRIDAY_BACKEND_WS_URI` fallback. Run
`make tls-local LAN_IP=<new-address>` after a Mac DHCP address change to issue a
new server certificate from the same CA. That does not require a firmware
rebuild unless the CA itself changes. If a saved endpoint repeatedly fails
before authentication, the firmware retries UDP discovery after five
disconnects, switches the running WebSocket client to the discovered address,
and persists it only after receiving authenticated `ready`. The threshold is
`CONFIG_FRIDAY_REDISCOVERY_FAILURES`.
