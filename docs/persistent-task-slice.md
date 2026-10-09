# Persistent Friday task slice — isolated fixture proof

This implements the first orchestration proof from persistent-mac-task-assistant-plan.md. It is not the complete Jarvis-like product, a Mac executor, phone login/remote service or a claim of sentience.

## Implemented behavior

A stable task ID, SQLite checkpoints and events survive a client disconnect and application restart. Exact scope review binds task/revision/fixture+executor digests, expiry and a single-use nonce. Duplicate start/approval IDs return the same task; conflicting IDs reject. A deterministic plan inspects an original arithmetic fixture, observes its failing check, diagnoses the known boundary error, prepares an isolated patch and checks the changed fixture. The original remains unchanged. Results include expected/actual values and original/final digests. Each task's conversation can explain those same stored facts from Phone/Mac/Device-text-harness client labels. No LLM was called and these labels are not authenticated devices.

The phone-sized control client is real HTML/CSS/JavaScript wired to the same task API, with start, clarification, full scope review/approval, progress, checked results, questions and Stop. Its actual JavaScript was driven against the actual in-process FastAPI/SQLite service through a stdio test bridge. No listener or authentication bypass was introduced. This is an isolated, fixed-owner test harness, not a deployed controller: it has no login, external connectivity, credentials or native-device speech/LCD wiring. app.main is unchanged; the separate create_demo_app defaults disabled and rejects non-loopback, cross-origin and malformed requests. Do not deploy this fixed-owner harness or interpret its custom request header as authentication. Actual owner/client enrollment and secure remote access remain separately gated.

Stop does not wait for a model or another UI request. Different clients' Stop requests remain Stopping until owned fixture work drains; cancellation generations/fencing prevent late evidence or subsequent steps. Deadline/lease-expired workers cannot commit or fail a current task. Crash recovery only replays these explicitly pure arithmetic/atomic-DB operations. The supervisor uses an exclusive database lock so another process cannot falsely assert work has drained. Fixture/source drift invalidates dependent artifacts and requires fresh exact approval; expired approvals can be explicitly refreshed. Nothing in this slice establishes safe replay or process-tree termination for a future OS executor.

Phone logic rejects stale task revisions and out-of-order selection replies, retains a Stop identity on uncertain network outcomes, and keeps Stop available during another request. Fetch deadlines cover response bodies. Offline state shows last-known data and unconfirmed Stop; no action is queued for automatic later replay. Existing memory/consent/source-peer presentation code remains unchanged. This task domain does not yet import real personal memories or publish through FridayAgent/native scene envelopes; those integration steps are in the architecture plan.

## Scope and evidence

Executor accepts only two registered original synthetic arithmetic fixtures. It cannot accept paths, commands, repository code, network/model calls or publication. All fixture work is trusted arithmetic and private SQLite artifacts; no subprocess/project code is executed. This is structural limitation, not proof of an OS sandbox. A general runner, actual selected-project analysis, paid/local model reasoning, cross-owner authentication, VAD repair, voice acceptance, native task presentation and away-from-home phone access are not implemented or proven.

Validation on final code:

Validation for the published branch is documented in [task-prototype-publication.md](task-prototype-publication.md). Native task presentation and device/voice integration are unbuilt. No firmware change is included.

## Reviewable demo and tests

The optional export script generates a self-contained synthetic transition replay under outputs/task-slice. Recorded captures and raw local logs are excluded from publication. The replay disables live controls and does not establish phone deployment or authentication.

The working control implementation is backend/app/static/task-phone.* and backend/app/task_demo.py. Run its stdio test harness with the already available dependency interpreter; it drives all core controls through the actual service without exposing an endpoint:

```
FRIDAY_TEST_PYTHON=/path/to/existing/venv/bin/python node scripts/test_task_phone_service.js
node scripts/test_task_phone.js
PYTHONPATH=backend:backend/tests /path/to/existing/venv/bin/python -m unittest test_task_slice
/path/to/existing/venv/bin/python scripts/export_task_demo.py
```

Do not copy production .env, tokens, database or TLS material into this demo. Task fixture database files are0600 and bounded to32tasks/100events each plus1000 ordinary action receipts and turns per demo owner, with up to32 additional Stop receipts. Ordinary limits stop admission. At1032 action receipts, emergency Stop still executes without storing a new receipt; exact identity conflict/dedup guarantees apply only to retained receipts. Old completed tasks can retire after seven days, with their retry receipts; replayed starts then create new unapproved work. Active, uncertain, unresolved-process and pending-delivery work never retires automatically. No real account setup or persistent credential is created. Use new isolated test databases, not a production database.

## Next meaningful implementation gates

First observe and repair voice/VAD reliability separately; this loop does not fix it. Then add real owner/client enrollment, authoritative task references to FridayAgent and protected native task presentation, plus a provably contained typed project runner. The first real selected-project acceptance stops at a checked patch. Personal meeting-prep/reminder continuity is the next domain proof, not an unrelated chatbot. Approve concrete execution/data-sharing/model-cost scope and remote connectivity setup separately. Any paired production installation also requires separate approval. The prototype is published for review only; no live backend/device installation, external port, system permission, paid inference, audio capture, new software or merge occurred.

The arithmetic-only demo now moves request storage operations off the async event loop and waits for committed storage changes or worker completion instead of checking every 50 milliseconds. Expiring running leases provide a recovery deadline. On restart, a stopping task absent from the process's in-memory worker map remains honestly `stopping`: absence is not subprocess termination evidence. Only the owning operation's actual drain or a separately established proof can finish cancellation. This demo still uses the arithmetic worker, not the trusted fixed-fixture subprocess executor, and remains a loopback harness without remote authentication.
