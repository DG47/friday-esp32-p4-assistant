# macOS local operations

Friday can run as a user LaunchAgent, using Apple's supported per-user launchd
mechanism: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
It starts when this user logs in and runs while the user is logged in. It does
not provide service before login, while the Mac is asleep, or after logout.

`scripts/run_backend.py` runs one foreground Uvicorn process, without the development
reloader. It reuses the repository's existing `.env`, database, TLS files, host and
port. No credentials are copied into the plist. The approved runtime now uses local Mac speech (see `local-voice.md`);
weather remains disabled. The generator defaults to disabled speech unless
`--tts-provider macos` is explicitly chosen. No OpenAI speech is enabled.

Generate a definition, after checking the runtime and existing TLS files:

```sh
python3 scripts/macos_service.py --output "$HOME/Library/LaunchAgents/com.friday.backend.plist"
plutil -lint "$HOME/Library/LaunchAgents/com.friday.backend.plist"
```

Stop any existing Friday listener before bootstrapping; never start a second
instance against the same port. Installation, removal and deliberate interruption
of a running service require the operator's authorization.

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.friday.backend.plist"
launchctl print "gui/$(id -u)/com.friday.backend"
curl --cacert data/tls/friday-ca.crt https://127.0.0.1:8000/ready
```

KeepAlive restarts exited processes, with a 15-second throttle to prevent rapid
failure loops. It does not detect a process hang. `/ready` checks workers and DB
connectivity; off-host alerts and a health-based watchdog are not configured.
Operational logs rotate at 2 MiB with three backups under
`backend/data/logs/backend-service.log`, created with private permissions. Request
access logging is disabled; the existing privacy-safe scene/action metadata remains.
No serial/audio recording service is installed.

To stop supervision without immediate restart:

```sh
launchctl bootout "gui/$(id -u)/com.friday.backend"
```

For a code update, validate first, then use
`launchctl kickstart -k "gui/$(id -u)/com.friday.backend"` deliberately; the device
briefly disconnects. To disable future login startup, boot out and archive/remove
only this plist. Reverting to `make dev-local` requires first booting out the agent.
Do not run both workflows simultaneously.

Backups use `scripts/backup_database.py` and refuse overwrite. Restore tests must
use a disposable copy, check integrity, and exercise a write/rollback without
replacing live state. Actual recovery requires stopping the service, preserving
the current database and WAL files, and approving replacement. Off-host encrypted
backup storage, retention and alert destinations still need operator choices.

The original CA lacked the key-usage extension required by Python 3.13+ strict
X.509 verification. The approved local repair reissued the CA public certificate
with critical CA basic constraints and keyCertSign/cRLSign usage, preserving its
key, subject, serial, identifiers and validity dates. The server certificate was
preserved. Normal/strict OpenSSL and strict Python HTTPS verification passed.
Public device trust was updated together; no system-wide root trust was installed.
The setup script now specifies these extensions explicitly for future CA creation.
Do not disable verification or replace credentials to address a compatibility error.

The GitHub workflow runs provider-free backend tests and a native C recoverable
error policy test. It is not an ESP-IDF cross-build or physical hardware test.
The workflow's server-side execution remains unverified until its changes are
approved for commit/push. Real visible pixels, physical touch, audible output,
Wi-Fi loss, speech echo cancellation and overnight behavior remain separate gates.
