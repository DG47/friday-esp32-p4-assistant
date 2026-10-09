# Task and Telegram prototype publication

The goal is a continuously available assistant that carries approved work through across Mac, phone/Telegram and the native device, with one persistent task identity and safe context continuity. The design extends beyond coding to personal organization. This PR preserves that direction and a disconnected regression prototype; it does not deliver general Mac autonomy.

## Base and isolation

Based directly on remote main `29c48ec`. Earlier development checkout `80f0258` plus a local installed-latch repair produced the historical529-test result. Those unrelated firmware/backend changes are excluded: existing provider interfaces on main suffice. No previous task PR is required; this new PR includes the previously local-only task modules, phone harness, tests and architecture documents as its essential standalone dependency. Production app startup never imports or enables the prototype. No private transcripts, databases, logs, credentials, captures or personal absolute paths are included.

## Confirmed critique and corrections

- Blocking runtime SQLite operations and writer-reserving reads: runtime work moves off-loop with cancellation draining; reads use read transactions. Store changes wake workers and planning instead of repeated short polling.
- Telegram missing help/errors/task choice and unreadable approvals: help/tasks, safe bound errors, unambiguous current Stop, complete labelled scope, callback acknowledgement attempts and fresh full-scope delivery after planning.
- Short polling/lock contention: independent20-second getUpdates polling and outbound loops; long polling holds no authority/send lock.
- Capacity and poisoned-update stalls: durable rejection decisions, consumption high-water, confirmed-only retirement, Stop reserve; malformed callback containers cannot block subsequent Stop.
- Partial replacement/process uncertainty: replay accepts only an exact fixed replacement prefix; persisted launch/owned/reaped records, heartbeat, fencing and actual drain guards; unresolved ownership survives retention and recovers uncertain.
- Misleading sandbox/egress claims: allow-default profile explicitly unsafe for untrusted code; generated code rejected; default transport withholds raw executor output. Deny-default containment is future design.

Independent security review of the isolated corrected prototype passed102 task tests. Independent lifecycle review passed83 initial focused tests and9 final storage tests; found and verified the Stop-receipt cap correction. Publication diff receives a separate independent review.

## Reproduce

Use an existing environment with backend/requirements.txt available. These tests use mocked providers and synthetic fixtures, never credentials or live Telegram.

```sh
mkdir -p outputs
PYTHONPATH=backend ASSISTANT_PROVIDER=mock TTS_PROVIDER=none WEATHER_PROVIDER=none STT_PROVIDER=none python -m unittest discover -s backend/tests -v
PYTHONPATH=backend python -m unittest discover -s backend/tests -p 'test_task_*.py' -v
node scripts/test_task_phone.js
FRIDAY_TEST_PYTHON="$(command -v python)" node scripts/test_task_phone_service.js
python -m compileall -q backend/app scripts
git diff --check
```

Clean main-based publication tree:485 backend tests pass, including102 task tests on macOS, plus both phone checks. The historical529 result applies only to the earlier development baseline. Real subprocess tests require macOS sandbox-exec; Ubuntu explicitly skips12 platform-specific tests and does not substitute an unrestricted runner. A dedicated macOS CI job exercises them. Individual denied probes do not establish comprehensive containment.

## Remaining work and limits

Only deterministic registered arithmetic/Python fixtures, one accepted planning enum and fixed repair bytes execute. Selected-repository analysis, generated patches and trustworthy untrusted-code containment remain unbuilt. No live Telegram setup/acceptance, credentials, production integration, authenticated remote phone, native task scene, real provider inference, VAD fix or deployment is included. Multiprocess transport coordination and production process reconciliation are unestablished.

At1032 receipts, new emergency Stop requests execute without retaining a new identity receipt; full conflict/dedup guarantees apply only to existing receipts. Ordinary outbox saturation may consume a durably rejected callback without an acknowledgement/error delivery. Stop remains admitted but its confirmation can be withheld when reserves fill. Uncertain process/delivery records require deliberate reconciliation and cannot be deleted automatically. Completed retries expire with seven-day task retirement; replay then creates new unapproved work.

Follow [deny-default-project-runner-design.md](deny-default-project-runner-design.md) for future containment and separately required permissions. Draft PR publication grants no merge, deployment, machine-permission expansion, bot setup or live call authority.
