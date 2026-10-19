# Separate inventory review candidate

This candidate is based on ca671a199d6dcb659dce7b9b8b1be55ad68176dc. It is
prepared in a separate repository for review and does not update draft PR23.
No broad conversation suppression, new integration, permission, schema or
firmware change is introduced. Overall production acceptance remains open.

The recorded synthetic natural local-model failure is in
`backend/tests/fixtures/recorded_inventory_claim.json`: one active owner reminder,
no tool calls, but prose claimed there were no active reminders. This is an
inventory assertion, not a claim that completion/cancellation executed. Earlier
operation-only passing cases never established that inventory prose was correct.
Six later exact-prompt repetitions did not repeat the claim; that does not erase it.

Explicit current inventory questions in the bounded forms "how many active/pending
reminders do I have", "do I have any/no active/pending reminders", "are there any/no
active/pending reminders [for me]" and show/list reminders render directly from
the existing scoped list tool. Other owners are excluded; records are not sent to
a model merely to count them. Empty is asserted only after a successful actual
empty read. Read failure or lost ownership is unverified, never empty.

When a model-selected list was observed, it is reread at final presentation after
all model/chat waits, bound to the original workspace instance and current request.
The returned list surface is refreshed as well as the count. Replacement,
disconnect or eviction/same-ID reuse cannot revive the old list. This is a read
snapshot at rendering, not an atomic guarantee against mutations after delivery.
Already admitted action receipts and Stop/consent fences remain independent.

The exact unsolicited false-empty construction is handled with a deliberately
bounded projection. Outside quoted/reported/history/conditional/uncertain or
negative-instruction scopes, current existential/possessive constructions such as
"there are no/zero/digit active/pending reminders" and "you have no/zero/digit
active/pending reminders" become "the current reminder inventory has not been
checked" if no actual list result supplies the final receipt. Surrounding prose
is retained. If mixed chat contains a covered claim after a verified final list
read, the covered span instead states that actual checked count; it must not
falsely say inventory was unchecked when the response already supplies evidence. This is not a reminder-word ban or a semantic truth oracle. No model
label or absence of tool calls is accepted as evidence of an empty list.

The output recognizer is not broadened to arbitrary paraphrases: "Your reminder
list is empty", "Nothing is awaiting your attention" and "You have two pending
reminders" remain outside the contract. Complex linguistic scope, other languages
and arbitrary chat factuality are unproved. Appending a disclaimer to a false
claim would not repair it; the covered unsupported span is replaced instead.
Do not describe this finite mitigation as universal inventory correctness.

Reproducible provider-free checks are in
`backend/tests/test_reminder_inventory_investigation.py`, including the verbatim
recorded output, positive/negative inventory, actual stale-list refresh, lost
context, other-owner exclusion, read-failure redaction and ordinary chat scopes.
Run the ordinary aggregate suite for all source regressions. The optional manual
`scripts/test_local_reminder_inventory.py --source-sha <exact-sha> --output <json>`
uses the existing cloud-disabled local model, unchanged instructions/tools and
disposable synthetic SQLite. It distinguishes deterministic direct routes from
natural model-selected list queries and does not measure universal factuality.
No model download, credentials, hosted CI, account call or live state is required.
