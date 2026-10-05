# Read-only Google Calendar

Friday supports real Google event reads and contextual cards. Account connection
and local provider activation are explicit operator steps; repository defaults
remain unconnected. Anthropic reasoning and local Mac speech remain the
established providers.

Default `CALENDAR_PROVIDER=none` is honestly unconnected. Set `sample` only for an
explicit, labeled preview. After a separately approved account connection, `google`
uses one calendar: `CALENDAR_GOOGLE_ID=primary` by default. No calendar writes,
CalendarList access, Gmail, invite responses or calendar mutation tools exist.

## Connection approval and least privilege

Before any setup action, approve the specific Google account and a user-owned
Google Cloud project/Desktop OAuth client, read-only event access, and private local
refresh-token storage. The app asks only for:

`https://www.googleapis.com/auth/calendar.events.readonly`

Google describes this as viewing events on all calendars the account can access.
The OAuth scope cannot be limited to one calendar; this implementation only queries
the explicitly selected calendar (`primary` initially). It does not request account
identity, calendar settings/list, email or write permissions. If broader grant scope
is returned, the connection helper rejects it.

Supported setup, **only after action-time approval**:

1. Choose the Google account and project. Enable Calendar API, configure OAuth
   consent for this private app, and create a **Desktop app** OAuth client. A Workspace
   administrator may need to permit the app. Do not reuse ChatGPT/dot connector
   credentials or ask the user to paste secrets into chat.
2. Download the user-owned client JSON locally. Place it in the private
   `data/google-calendar/` directory (0700), with file mode0600. Never commit it.
   The helper accepts Google's `installed` client format; supplied endpoint URLs
   are ignored in favor of fixed official Google endpoints.
3. Review the actual account/storage destination, then run from the intended repo:

   ```sh
   .venv/bin/python scripts/connect_google_calendar.py \
     --client data/google-calendar/client.json \
     --destination data/google-calendar/credentials.json \
     --approve-account-access
   ```

   The helper opens the system's default browser and listens temporarily on
   `127.0.0.1` at a random port. PKCE S256 and a random state protect the authorization
   response. It times out, logs no callback codes and never listens on the LAN.
   An existing credentials file is refused rather than overwritten. Do not run
   this command as part of tests or without approval.
4. Store a refresh token plus the Desktop client's ID/secret in that mode0600 file,
   inside the owned mode0700 directory. This file is permission-protected, **not
   encrypted at rest**. Access tokens remain in memory. No system root trust,
   Keychain entry, service account or new network/firewall policy is needed.
5. Separately approve local configuration/restart. Use `CALENDAR_PROVIDER=google`,
   `CALENDAR_GOOGLE_CREDENTIALS_PATH=data/google-calendar/credentials.json`, the
   agreed `ASSISTANT_TIMEZONE`, and the selected calendar ID. Keep weather disabled
   and the supervised `TTS_PROVIDER=macos`; don't switch reasoning away from Anthropic.
   Test one bounded real read only after the account-access approval.

Google OAuth testing-mode/offline-grant expiration and Workspace policy may limit
long-lived access. The app shows revoked/unconnected states rather than silently
using preview data. For disconnect, revoke this app's grant in Google's account
permissions, then deliberately remove/archive local credentials and disable the
provider. Revocation clears in-memory calendar/action caches when observed; old
local histories/backups are not automatically erased. Explicit model-sharing can
be disabled independently.

## Local versus Anthropic data

`CALENDAR_MODEL_CONTEXT=false` is the default. Deterministic requests such as
“show calendar” and “what is next” read Google directly and produce native cards
and local Mac speech, without a Claude/model call. Fetched event details are withheld
from reasoning tool results. These deterministic exchanges store a generic history
answer instead of fetched titles. Calendar-tagged exchanges from previously enabled
sharing are excluded from future model context when sharing is disabled.

Google supplies event IDs, titles, start/end dates/times, recurring-instance flags,
locations and attendee display names. Descriptions, attendee email addresses,
attachments and extended private metadata are not requested. Event snapshots are
cached in Mac memory (fresh60seconds; transient failures may use explicitly stale
cache for up to10minutes). Paired Friday devices and the protected simulator/API can
see the selected calendar. Event touch tokens are bounded, short-lived and scoped
to the requesting device. Cards expire back to ambient after30seconds.

The existing durable assistant-job database may store displayed cards and spoken
reply text for typed/voice requests, even with model sharing disabled. Terminal
job retention is7days/10000 rows as in the existing delivery design; cleanup runs
with the job worker. Touch-only calendar scene reads don't create conversation
exchanges or durable model jobs. Operational logs contain status/action categories
and counts, not calendar titles, contents or OAuth secrets. Synthetic QA artifacts
are labeled fixtures and contain no actual calendar data.

Only if separately approved, `CALENDAR_MODEL_CONTEXT=true` allows calendar facts
(up to32 event records per model tool result) into Anthropic context for richer
reasoning/follow-up. The resulting tool exchanges and assistant replies persist in
local conversation history, which currently has no automatic retention policy;
normal local backups can also contain them. Disabling sharing excludes tagged
calendar exchanges from future model context, but does not delete old local data
or retract content already sent to Anthropic. User-written calendar details in a
normal model-directed message remain ordinary user input to the reasoning provider.

## Display/read behavior and bounds

Today uses midnight-to-midnight in the assistant's IANA timezone; DST days can be
23 or25hours. Timed events convert to the display timezone. All-day dates preserve
Google's original date fields and exclusive end date, with the calendar's timezone
used for matching instants. Recurrences are expanded server-side (`singleEvents`),
cancelled instances excluded, and empty API pages with a next token still followed.
The next-event read looks seven days ahead and includes ongoing events.

API reads are bounded to10 pages/1000 events,1MiB per page and20seconds total;
page size100. Partial results are labeled limited rather than reported as an empty
calendar. The UI uses four cards and up to four touch buttons: event details,
Previous/Next, Refresh and Dismiss. No unavailable Prep/Email/Move buttons pretend
to work. Fresh, stale, empty, unconnected, revoked and transient error states differ.
Event text is data, not instructions; browser text uses `textContent`.

Protected `/api/calendar/today` now returns a status/source/freshness envelope with
`events`, `event_count`, `truncated` and `fetched_at`, replacing the old bare sample
list. Device scene kinds remain existing `timeline`, `artifact`, `message` and
`ambient`; the native renderer's four-card/four-button/64-byte-action protocol is
unchanged. New backend action IDs are `calendar:N`, `calendar_refresh`,
`calendar_next`, and opaque `event:<16hex>`.

## Evidence and limits

Fixtures/mock HTTP are used for all Google/OAuth tests. Installed Google Chrome
with disposable profiles runs browser action/layout checks and produces labeled
screenshots through `scripts/calendar_qa_fixture.py` and `scripts/visual_qa.py`.
No account grant or live Google read is claimed. Native firmware error-policy and
scene/action buffer compatibility are checked; there is no new flash or actual
ESP32 calendar/touch/pixel validation in this candidate.

Official references:
- https://developers.google.com/workspace/calendar/api/v3/reference/events/list
- https://developers.google.com/identity/protocols/oauth2/native-app
- https://developers.google.com/workspace/calendar/api/auth
- https://developers.google.com/workspace/calendar/api/concepts/events-calendars
