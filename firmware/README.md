# P3A firmware

This is the ESP-IDF firmware for the Waveshare
ESP32-P4-WIFI6-Touch-LCD-4B/P3A. It uses Waveshare's published 3.0.1 BSP for
the ST7703 MIPI display, GT911 touch controller, and board pin definitions.
The current hardware milestone renders Friday's native animated state scene,
supports touch interaction, provides first-boot Wi-Fi provisioning, and captures
live audio from the board's ES7210 microphone array.

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
5. Done: ES7210 microphone capture at 24 kHz, with live peak/RMS levels on
   screen and in the serial log.
6. Next: stream microphone frames to the backend JSON WebSocket protocol and
   add WakeNet9/VAD plus cloud STT.
7. Attach an MX1.25 2-pin 8Ω/2W speaker before enabling ES8311 playback.

## First-boot Wi-Fi setup

With no saved credentials, the device creates this temporary setup network:

- Network: `Friday-Setup`
- Password: `fridaysetup`
- Setup page: `http://192.168.4.1`

Submit the local Wi-Fi name and password in the setup page. The device stores
them in its NVS partition, restarts, and switches to station mode. NVS
encryption is not enabled in this MVP. After five failed connection attempts,
the device clears the saved settings and returns to setup mode.

The P4 host libraries are deliberately pinned to `esp_hosted` 2.9.0 and
`esp_wifi_remote` 1.2.5 to match the factory ESP32-C6 hosted firmware. This
removes the host/coprocessor protocol warning and has been verified in both
access-point and station modes. Do not upgrade or flash the C6 independently
without obtaining the matching board-specific image.

The browser simulator is the source of truth for interaction behavior; the
firmware renderer consumes the same scene/state protocol.
