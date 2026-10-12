# Production report Telegram connector: local setup and acceptance boundary

The connector is implemented and disabled by default. It uses fixed HTTPS Bot API
long polling, not a webhook or inbound public endpoint. HTTP contract tests use
`httpx.MockTransport`; they are not live Telegram tests. No credential has been
received and no live message has been sent for this candidate.

## Owner steps after separate rollout approval

1. Create a dedicated bot with the official [BotFather](https://core.telegram.org/bots/features#botfather).
   Keep its token out of chat, shell arguments, environment variables, `.env`, repository
   files, screenshots and logs. Use a dedicated bot with no webhook or other consumer;
   remove an existing webhook through a separately authorized Telegram administration
   workflow before starting long polling. This connector does not delete webhooks.
2. Verify your numeric Telegram user ID and corresponding private chat ID out of band,
   against your already verified Friday owner identity. They must match. A username,
   display name, forwarded message, `/start`, or incoming text never enrolls an owner.
   The CLI accepts only an operator-verified positive numeric private user ID.
3. Choose an existing private local secrets directory outside the repository, owned by
   your macOS user with mode 0700. Run the following in your local interactive terminal,
   substituting only nonsecret directory/owner/ID values. Use the project's Python
   environment; do not type a token in the command itself:

   ```sh
   python scripts/setup_report_telegram.py --directory /absolute/private/directory --owner VERIFIED_OWNER --private-user-id VERIFIED_NUMERIC_ID --verified-out-of-band
   ```

   The token prompt hides input and refuses piped/nonterminal input. Confirm `VERIFIED`
   only after checking the numeric identity. The script writes exclusive 0600
   `bot-token` and `telegram.json` files and performs no API request or activation.
   It refuses existing files and never changes OS permissions or creates parent
   directories. The selected token/config paths reject symlinks and hardlinks.
4. Review the private `telegram.json` locally. It contains version 1, an absolute
   `token_path` and explicit bindings with `user_id`, matching `chat_id`, existing report
   `owner`, and `verified_private_owner: true`. Keep credential and identity state local.
   Review consent for exposing task goals, enrolled relative filenames, manifests,
   scope metadata and result SHA to Telegram. Source/report bodies remain local.
5. Choose a real project folder and exact supported nonsecret files through the trusted
   local operator workflow described in `project-report-vertical-slice.md`. There is no
   project-picker UI. Review exclusions and keep source separate from private state and
   output directories. Map the physically paired device to the same verified owner.
6. After separate approval, the rollout operator configures the existing backend:
   `ASSISTANT_REPORTS_ENABLED=true`, explicit project enrollment path,
   `ASSISTANT_REPORTS_ALLOW_REAL_PROJECTS=true` only for the reviewed real folder,
   `ASSISTANT_PROVIDER=local-only`, `ASSISTANT_REPORT_TELEGRAM_ENABLED=true`, and
   `ASSISTANT_REPORT_TELEGRAM_CONFIG_PATH=/absolute/private/directory/telegram.json`.
   These values contain no bot token. Production startup fails closed on invalid
   enrollment, concurrent supervision or incompatible bot identity. Readiness and
   planning fail closed on unavailable local model/cloud-disabled policy. Use a fresh report task database for the first production bot; mock rehearsal
   state cannot silently become production state.
7. Separately authorize a live acceptance exercise: `/help`, `/report ALIAS GOAL`, read
   the entire exact scope, approve its button, `/status TASK_ID`, device Recover, and
   `/stop TASK_ID` on an unapproved test task. Validate reconnect and failure behavior
   before relying on the phone. Hardware/native display acceptance is separate.

## Delivery contract and remaining limits

[Official Bot API](https://core.telegram.org/bots/api#getupdates) documents offsets,
long-poll timeout and the mutually exclusive webhook mode. The implementation persists
its offset only after a durable admission/decision, and sends it on the next poll.
Repeated update IDs cannot create another task. Invalid/unbound inputs advance through
recorded refusal without becoming owners. Polling requests message/callback updates
only, timeout 20 seconds, batch 25, client deadline 35 seconds. A polling network failure
retains the offset and backs off up to 30 seconds. Flood-control `retry_after` is persisted
and bounded; malformed values fail conservatively.

[sendMessage](https://core.telegram.org/bots/api#sendmessage) sends plain text with
previews disabled, split below the documented limit. A complete scope wave must be
confirmed before its approval keyboard is sent. A recipient/revision/nonce change
invalidates old approval. Response groups, binding generations and delivery state
survive restart. Stop has reserved queue capacity and revokes the existing durable job.
The same owner/task is projected through the actual device dispatcher.

Only a positive API receipt is recorded as sent. Timeout, cancellation, 5xx, malformed
reply or interrupted sending is uncertain; it is not automatically resent. Only explicit
[429 flood-control rejection](https://core.telegram.org/bots/api#responseparameters)
uses bounded retries (maximum three send attempts). Telegram has no sendMessage
idempotency key in this implemented contract, so exactly-once messages are not promised.
An owner can explicitly request current status/full review using a new command after
uncertainty; that is a new delivery, not evidence that the earlier message was absent.
An uncertain scope body withholds its keyboard. Source execution certainty and message
certainty remain distinct.

HTTPS verification is enabled, proxy environment and redirects are disabled, methods
are limited to getUpdates/sendMessage/answerCallbackQuery, response bytes and deadlines
are bounded, and token-bearing HTTP logs are redacted. API descriptions/errors are not
logged or persisted. 401/403/409 polling failure terminates the consumer and blocks new
report admission/approval. Existing verified results remain results; failure never
fabricates completion. Uploaded/forwarded content, file exports, arbitrary commands
and text-driven privilege changes are explicitly rejected. Planner tools still cannot
change enrolled paths, owners, network permissions or execution capabilities.

Both task-supervisor and protected-config-directory consumer locks precede any binding
mutation. A competing local startup cannot rebind the active owner. Unchanged verified
bindings preserve generation across restart; removals/reassignments revoke old grants.
A fingerprint pins this task database to its bot credential. Token rotation fails closed
and currently needs a separate reviewed migration/new task database, not blind reuse.
The config-directory lock protects cooperating local instances using that directory;
remote consumers and duplicated configurations cannot be proven absent. Dedicated-bot
single-consumer ownership is an operational requirement, with API conflict failing closed.
SQLite/file containment assumes the private directories are not concurrently modified
by a hostile process with the same macOS user privileges.

Remaining rollout gates are owner-entered credentials and verified enrollment, approved
real-folder selection, live Telegram acceptance, hardware acceptance and separately
approved backend deployment. Connector implementation and synthetic production-path
HTTP acceptance are complete; no live-delivery acceptance is claimed.
