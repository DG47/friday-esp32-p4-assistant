# Same-device context resume across backend restart

A controlled actual-agent scenario selected a Calendar event, explicitly corrected its
Weather ZIP, then restarted the agent against the same database. Main 649d184 loses
the relation and cannot continue a contextual Weather request. This candidate retains
only resume intent and forces fresh source reads before rendering facts.

The additive context_checkpoints table in the existing local SQLite database contains
an authenticated device ID, opaque event reference/source fingerprint, approved ZIP
correction, original today/next query range and an absolute 24-hour expiry. At most
32 rows remain; normal activity updates intent, loading does not renew it. No event
title/address/time/attendee, Weather measurement, conversation text, action token,
consent, credential or approval is checkpointed. Fingerprints are comparison hashes,
not an encryption or anonymization guarantee. Existing data-directory access controls
and private backup policy still apply. No new file location or permission is introduced.

A new context instance loads only intent. The first contextual Weather request forces
a fresh Calendar read of the original range and verifies that the selected instance
still exists. The exact source-location fingerprint must still match before its
explicit ZIP correction is reused. Changed/ambiguous/removed/moved-out-of-range events
need a new selection; a different sole event is not silently substituted. Weather is
read again; old measurements never return. Sharing-disabled model requests are rejected
before resume I/O. New request ownership is rechecked after the read. Restart rotates
the touch-token epoch; old actions are rejected. Explicit dismissal deletes intent;
expiry and another device ID cannot resume it.

The focused native-Python controller tests cover restart, source changes/removal,
privacy gate, superseded read, original range, expiry, dismissal and identity isolation.
The original resume regression fails on main. Aggregate tests/CI are recorded in the PR.
This is a bounded same-device relation repair, not cross-account/client identity merging,
full task replay, a persisted screen, or a general restart-persistent shared workspace.
The physical device/Mac route and actual model tool selection remain acceptance gates.

Existing jobs, confirmations and reviewed memories retain their separate durable stores.
No job is automatically retried, approval replayed, report/Telegram enabled, or folder
enrolled. If source freshness fails, reply honestly without reviving facts. The failed
read clears the old relation through the existing freshness-failure path; recovery may
require a fresh Calendar selection. A crash before checkpoint commit can lose latest
intent. SQLite failures fail the request; this does not promise uninterrupted operation
through storage failure or concurrent process execution.

Deployment belongs to the coordinating owner after exact revision/check review. Back up
the database privately with the online API and restore-check before backend-only update.
The table is additive; no existing data is rewritten. Older source ignores the new table,
but rollback loses restart-resume behavior. No firmware flash or trust/NVS change is
required for this source repair. Local-only policy and disabled integration defaults stay.
