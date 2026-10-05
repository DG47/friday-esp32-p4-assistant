# Local Mac speech

The approved local runtime uses `TTS_PROVIDER=macos`, preserving Anthropic for
reasoning. No OpenAI speech API requests are made. It uses existing `/usr/bin/say`
and `/usr/bin/afconvert` with the installed Daniel (English (UK)) voice. No voice
assets or dependencies were downloaded. Local speech has no speech-service fee;
ordinary Anthropic reasoning remains a separate cloud API service.

Configuration: `TTS_LOCAL_VOICE` (installed exact name), `TTS_LOCAL_RATE` (100–250,
default180), `TTS_LOCAL_GAIN` (0–1, default0.25). The tested reduced software gain
is not a calibrated sound-pressure level. The speaker codec retains its established
volume40; firmware was not changed for local voice.

The supervisor definition selects local speech with
`python3 scripts/macos_service.py --tts-provider macos --output /path/to/new.plist`.
Generate a new definition, then deliberately reload the existing user service;
wait for bootout to finish before bootstrap. The foreground launcher permits only
local Mac speech or disabled speech, so it cannot accidentally enable OpenAI TTS
through `.env`. Weather remains disabled. Existing TLS correction is preserved.

Synthesis sends text through a private 0600 temporary file inside a 0700 directory,
never a shell or text-bearing process argument. Output/conversion subprocesses
have bounded lifetime and output files; cancellation kills/reaps the owned process
and removes temporary text/audio. Text is limited to1000 characters; total synthesis
30seconds; temporary output8MiB; final PCM30seconds, mono16-bit little-endian24kHz.
Responses exceeding the duration bound fall back to the written reply. No content,
PCM or transcript is logged; playback logs contain device/status/byte-count metadata.
Temporary synthesis audio is not a recording of room audio and is not retained.

The adapter fails clearly when selected on non-macOS or missing system tools;
other platforms can keep TTS disabled. This local runtime still depends on the
Mac being awake and the user logged in.

Device completion acknowledgement proves the playback task reached successful
codec writes. It does not prove audible sound, correct wiring/acoustics, physical
touch interruption or comfortable volume. Current playback is half duplex: wake
capture is suppressed during speech; acoustic echo cancellation and spoken barge-in
are not implemented. Touch cancellation and stream invalidation already exist.

Wake detection is VAD-gated on the ESP32: it sends short candidate-room-speech
windows (with limited pre-roll, minimum3seconds and maximum12seconds) over the
existing authenticated TLS LAN connection to the Mac for local Whisper recognition.
It does not continuously transmit silence. Wake-probe transcription stays on the
Mac; recognized user requests and recent conversation/tool context can go to
Anthropic for reasoning. Local TTS stays on the Mac. Room audio is not logged.

Conversation records persist locally and the model reads bounded recent context.
Explicit reviewed long-term memory is implemented and enabled. The supervised
Google Calendar integration is enabled with model sharing disabled; its existing
grant authorization passed a content-free check. Gmail and Home Assistant are not wired. Timers/reminders
are real local capabilities; weather's real adapter remains disabled pending setup.

An isolated default-off echo-cancellation/spoken-wake-interruption candidate now exists; see `voice-reliability.md`. The separately approved controlled trial has experimental AEC enabled and metadata diagnostics installed. Physical echo suppression and delayed spoken-wake interruption remain unverified; half duplex remains the default for new builds.
