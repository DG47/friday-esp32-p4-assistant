# Operations

What keeps a personal Friday installation running, and what to do when it does not.
Everything here is local to the Mac that runs the backend. Nothing phones home.

## Logs

`scripts/run_backend.py` writes `backend/data/logs/backend-service.log`, rotated at
2 MiB with three generations kept. Alerts appear there at `ERROR` level with the
kind and a short message, never reminder text or conversation content.

## Backups and retention

The backend backs up its SQLite database on a schedule using SQLite's online
backup API, so the running service is never stopped or locked for long.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASSISTANT_BACKUP_INTERVAL_SECONDS` | `86400` | Create a backup when the newest one is at least this old. `0` disables scheduled backups. |
| `ASSISTANT_BACKUP_DIRECTORY` | `data/backups` | Where backups are written, mode `0700`. |
| `ASSISTANT_BACKUP_KEEP_COUNT` | `14` | Keep at most this many backups. |
| `ASSISTANT_BACKUP_KEEP_DAYS` | `30` | Delete backups older than this. The newest is always kept. |

Files are named `friday-YYYYMMDDTHHMMSSZ.db`, written with mode `0600`, and
integrity-checked before the previous ones are pruned. A failed backup is counted,
logged by exception type only, and raised as a `backup_failed` alert. The scheduler
checks once a minute and waits one minute after startup before its first check.

A manual backup at any time:

```sh
.venv/bin/python scripts/backup_database.py data/friday.db data/backups/manual-$(date -u +%Y%m%dT%H%M%SZ).db
```

### Restore

Restore into a **new** path and point a fresh backend at it. Never copy a backup over
the database of a running service.

```sh
launchctl bootout "gui/$(id -u)/com.friday.backend"
.venv/bin/python scripts/backup_database.py data/backups/friday-20261030T120000Z.db data/restored.db
ASSISTANT_DATABASE_PATH=data/restored.db make dev-local   # verify, then update .env and bootstrap the agent again
```

Device identities, pairing state and reminders travel with the database. The TLS
material under `data/tls/` is separate and is not part of a backup.

## Metrics

`GET /metrics` returns Prometheus text format. Like the other private reads it is
loopback-only and needs the simulator or admin token:

```sh
curl --cacert data/tls/friday-ca.crt -H "X-Friday-Simulator-Token: $ASSISTANT_SIMULATOR_TOKEN" https://127.0.0.1:8000/metrics
```

It exposes counts only: uptime, whether every background worker is still running,
connected devices, agent jobs by status, pending reminders, reminder alerts sent,
backup state and operator alerts by kind. No reminder text, device identity or
conversation content is included. A local Prometheus or a `curl` in cron both work.

## Alerts

Two conditions raise an alert: a failed scheduled backup, and a background task
that has stopped (the same condition that turns `/ready` to 503). Every alert is
logged. To hand it to something that can get your attention, set a command. It is
run with two extra arguments, the alert kind and the message:

```env
ASSISTANT_ALERT_COMMAND=/usr/bin/osascript -e "on run argv" -e "display notification (item 2 of argv) with title (\"Friday \" & item 1 of argv)" -e "end run"
ASSISTANT_ALERT_COOLDOWN_SECONDS=3600
```

The same kind is not sent again within the cooldown. A command that fails or hangs
for more than ten seconds is logged and ignored; it can never take the backend down.
The Telegram report connector is a separate, owner-approved feature and is not used
for alerts.

## Health check

`KeepAlive` restarts a backend that exits, but not one that hangs. The health check
probes `/ready` over the pinned CA and, if you ask it to, restarts the LaunchAgent
after a run of consecutive failures.

```sh
.venv/bin/python scripts/check_backend_health.py            # exit 0 when ready
.venv/bin/python scripts/check_backend_health.py --kickstart-after 3
```

To run it every minute from launchd, generate and load a second agent:

```sh
python3 scripts/macos_service.py --health-output "$HOME/Library/LaunchAgents/com.friday.healthcheck.plist" --kickstart-after 3
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.friday.healthcheck.plist"
```

Failure counts live in `backend/data/health-state.json`. Without `--kickstart-after`
the probe only records and exits non-zero, which is enough for a cron mail or a
status bar script.

## What this does not cover

No metrics are shipped anywhere, no alert is delivered off the Mac unless your
command does it, and a restart by the health check is a blunt instrument that
resets the count. Soak testing on the physical board and the speaker path remain
open items in [TODO.md](../TODO.md).
