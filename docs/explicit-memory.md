# Explicit local memory candidate

Isolated candidate from f28c723726d9c296f90922d35bc049e5c13ea32e, disabled by default (`ASSISTANT_MEMORY_ENABLED=false`). No live service, database, credentials, calendar integration, or firmware was modified. Deployment needs separate approval. This is for one person's local Friday, using the existing paired-device ownership boundary; no new account or multi-tenant infrastructure.

## Concrete commands and review

- `remember: I prefer green tea` or `remember that I prefer green tea`
- `show memories`, `recall memory`, or `what do you remember?`
- `update memory #1: I prefer black tea`
- `forget memory #1`

Every write presents a paged preview with Cancel. Tap Next to review each text page; Confirm appears only on the final page. A server-side sequential review cursor blocks early or skipped-page confirmation, survives restart and remains bound to the exact record revision. Voice “yes” is not a memory confirmation. The status screen and simulator shortcut expose Memory. Tap a list entry for paged full text, provenance/timestamps, the edit command and Forget. Editing uses speech or simulator text input; no native keyboard editor.

Facts are limited to 500 printable ASCII characters, 100 active records per paired device, and four records per list page. Unsupported Unicode/control characters are explicitly rejected because the baseline native font cannot display them reliably. Detail and confirmation pages use the available two-line subtitle area, with exact native glyph widths, kerning, integer rounding, two pixels of letter spacing, and Friday's configured word-break rules. The paginator measures candidate row prefixes and prefers nearby natural word/punctuation boundaries. It inserts a display-only newline between the two rows to prevent pathological native long-word wrapping; the stored source remains unchanged. The simulator preserves these line breaks and intentional spaces. A very long word can span rows or pages. This replaces the former fixed 20-character chunks.

A host probe compiles the installed pinned LVGL 9.3 lv_text.c/font getter and actual Montserrat 24 font, with no firmware change. All 9,120 glyph/next-character measurements match the checked-in metric table. Python line boundaries match native LVGL across 3,877 synthetic strings; all 14,296 exact displayed pages measure at most 620 pixels wide and 54 pixels high in the real LVGL layout code, within Friday's 620x76 subtitle. The regular label's line spacing is zero. Previous confirmation cursors carry a layout version; a cursor from the old algorithm cannot authorize a new layout. See outputs/memory-qa/native-layout-report.json. Physical-device pixels remain unverified.

Measured page counts: the common 100-character fixture takes 2 pages (previously 5); a common 500-character paragraph takes 6 (previously 25); 500 unbroken widest letters (`W`) take 12; both the adversarial wide-word fixture and the pathological 22-letter-word fixture take 12. The maximum observed among the 500-character cases in the complete native corpus is 13 pages. These are measured cases and a corpus maximum, not a mathematical worst-case claim for every possible ASCII string. Short preferences usually fit one page.

Memory replies use the displayed page as `AgentReply.text`: both `agent.result` and the legacy surface/assistant.text sequence preserve that exact page when firmware calls `friday_ui_set_response`. The durable job-to-delivery path is regression-tested. No firmware source change or compilation is required.

## Storage, ownership and recovery

Enabled initialization adds explicit_memories, memory_versions and memory_confirmations to SQLite. Existing tables are unchanged. Records contain stable ID, authenticated device owner, explicit-user-request provenance, created/updated UTC timestamps, active/forgotten status and revision. There is no cross-device sharing or account mapping. Retrieval is deterministic pagination, with no embedding or semantic-search dependency.

Confirmations expire after five minutes, are bound to owner/revision, and are consumed transactionally. A new proposal supersedes its predecessor. Retried jobs reuse the proposal; resolved requests cannot duplicate writes. Updates archive the previous record; Forget hides the record and retains its text/tombstone and prior revision. No memory purge runs: tombstones, versions and confirmation payloads remain indefinitely. Active limits bound retrieval, not total disk retention. Ordinary job pruning does not erase memory tombstones or versions.

There is no Restore UI. The supported operator recovery path is `scripts/recover_memory.py`, which previews by default, requires an exact owner/record/archived revision, and refuses absent ownership or unsupported text. Preview reads the database in read-only mode. Apply requires a fresh, non-overwriting integrity-checked backup and the reviewed current revision. A stale revision fails, current content is archived, recovery creates a new active revision, and pending memory proposals for that owner are superseded. All recovery tests use synthetic databases.

Example operator commands, to be run only after separate approval and while Friday is stopped (substitute the actual database path and paired device ID):

```sh
python scripts/recover_memory.py /path/to/friday.db --owner friday-p3a --memory-id 1 --archived-revision 1
python scripts/recover_memory.py /path/to/friday.db --owner friday-p3a --memory-id 1 --archived-revision 1 --expected-current-revision 3 --apply --backup /path/to/new-before-recovery.db
```

Inspect available revisions with a parameterized local query against memory_versions for the exact owner and memory_id. The apply example assumes preview reported current revision 3. It does not permanently erase any real record.

## Exact model/privacy boundary

No rows are retrieved from explicit_memories, memory_versions or memory_confirmations into provider requests. Recognized explicit memory commands return before provider selection; their request text, memory replies and surfaces are excluded from conversation_messages. `agent_jobs` does retain accepted request text and reply_json (including local previews/list surfaces) under existing local job retention. Model context is constructed from conversation_messages, current ordinary input, system instructions and existing tools; it does not read agent_jobs. A test persists memory jobs, restarts the agent and captures the actual Anthropic adapter's mocked transport payload for a later ordinary turn, proving those memory markers are absent. No paid calls are made.

This is a retrieval boundary, not a universal content firewall: ordinary user input that quotes a remembered fact is sent as ordinary input, and pre-existing conversation history is not retroactively scrubbed. The same test positively verifies explicit user-supplied fact text does reach the mocked transport on an ordinary turn. Therefore an absolute claim that no memory-shaped text can ever reach Anthropic would be incorrect.

There is no memory model tool, automatic extraction, provider-sharing setting, background import, or use of real personal/chat/calendar data in this candidate. The browser renders facts with textContent; remembered instructions execute no tools. Recognizable credential requests are replaced with a fixed rejection marker before durable job ingestion; direct store validation also rejects credential patterns. This is a defensive check, not a universal secret detector. Users must not supply credentials or secrets.

Future provider sharing needs a separately approved, initially off capability and per-request approval showing the exact selected IDs/text, provider and purpose. Share a bounded data block, never system instructions; exclude tombstones, versions, confirmation payloads and other device records. That capability is absent here.

## Verification and safe enablement plan

Run `PYTHONPATH=backend python -m unittest discover -s backend/tests -q` with the existing environment. The exact final source passes 191 backend tests, including 19 memory tests and 4 paginator tests: additive migration/restart, ownership, expiry/replay/cancel/supersession, stale revisions, retained history, request retry, secret rejection before enqueue, capacity/Unicode bounds, paged-review enforcement, durable delivery, Anthropic context isolation and supported operator recovery.

`PYTHONPATH=backend python scripts/memory_qa_fixture.py` generates synthetic fixtures using production simulator HTML/CSS/renderer and recorded responses from the actual local controller. `python scripts/visual_qa.py outputs/memory-qa/fixture.html` runs 68 browser assertions: clicked action/typed edit routing, confirm/cancel/repeat/stale/disabled states, pagination, inert HTML text, and all 12 measured pages of a 500-character wide-letter fact. These are offline recorded-response browser tests; authenticated websocket confirmation/detail/forget and ownership are separately tested in Python. Screenshots cover list, detail, edit/forget confirmation, stale errors and disabled state. The Browser plugin's execution tool was unavailable; the repository headless Chrome runner used disposable profiles and no Friday connection.

Reproduce the native layout check with the installed LVGL directory (no downloads or installation):

```sh
python scripts/native_memory_qa.py --lvgl-root /path/to/firmware/managed_components/lvgl__lvgl --verify outputs/memory-qa/native-layout-report.json
```

The host probe measures layout, not bitmap rendering. Unused bitmap callbacks abort if called, so it cannot silently substitute a renderer. Runtime pagination uses only a small checked-in measured table and Python rules; the native compiler/LVGL source is needed for verification only.

Nothing has been applied. Minimal future plan:

1. Review this patch against the current source, including other isolated candidates; rerun the aggregate suite after integration.
2. With approval, stop Friday and create an integrity-checked SQLite backup using scripts/backup_database.py. Keep the backup and prior source revision.
3. Apply the reviewed source while memory remains off; set only `ASSISTANT_MEMORY_ENABLED=true` if enabling is separately approved, then restart once. Do not flash firmware for this backend-only change.
4. On the paired device, use a harmless synthetic fact; review all pages, confirm, recall, edit, cancel, forget, restart and verify tombstones/ownership. Validate actual speech and device pixels before storing useful personal facts.
5. Roll back by disabling the flag and restarting if needed. Retain the additive memory tables and backup; avoid restoring an old whole database over newer reminders/jobs without reviewing the data loss that would cause.

Remaining gaps: physical-device/speech QA, end-to-end live browser connection, restore UI, and user-facing retention/export policy. None requires live changes to finish this candidate.
