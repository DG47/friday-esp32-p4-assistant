# Personal Spotify playback and timed lyrics

Spotify Web API is the
primary account-wide source. The backend still runs on the Mac; Spotify desktop
and AppleScript are not runtime dependencies. The optional tested Mac adapter
remains unused. No ESP32 music streaming is implemented.

## Opt-in and activation

Default `MUSIC_PROVIDER=none` creates no HTTP client, accesses no grant file and
starts no music polling. User-run PKCE setup uses the approved Friday public app:

```sh
.venv/bin/python scripts/connect_spotify.py connect --client-id YOUR_APPROVED_PUBLIC_CLIENT_ID --port 8765 --destination "$HOME/.friday-private/spotify/refresh.json" --approve-account-access
```

This helper's import and `connect --help` were checked. It opens user consent,
uses PKCE and exact loopback redirect `http://127.0.0.1:8765/oauth/spotify/callback`,
and stores only the refresh grant in owner-only directories/file outside source.
No client secret. Do not paste credentials or callback URLs into chat. Existing
files are never overwritten. Storage is permission protected, not encrypted.
The dashboard reports a 180-day refresh-token lifetime; reauthorization may be
necessary. Local disconnect preserves the grant and disables use; Spotify account
revocation is separate. The connection helper alone does not activate the service.

After approval to apply/restart/flash, the operator uses the existing private
service configuration, with the resolved absolute token path (no `$HOME` literal
in an env file):

```dotenv
MUSIC_PROVIDER=spotify
MUSIC_ACCOUNT_ACCESS_APPROVED=true
MUSIC_CREDENTIALS_PATH=/absolute/private/spotify/refresh.json
MUSIC_TIMED_DISPLAY_ENABLED=true
LYRICS_METADATA_SHARING_APPROVED=true
LYRICS_CLIENT_IDENTIFIER=Friday/0.1 (personal timed-lyrics display)
MUSIC_POLL_SECONDS=3
MUSIC_VIEW_SECONDS=300
```

Metadata/controls can run with timed-display false; that invokes no lyric provider.
Scopes are only `user-read-playback-state user-modify-playback-state`. They grant
remote controls on existing Spotify devices; the feature never transfers playback,
starts a new context or chooses a device automatically. Only explicit authenticated
touch Play/Pause/Previous/Next performs a mutation. Command tokens are one use,
device scoped, expire in 30s, and recheck track/device/state/restrictions. Mutations
are never retried. Premium and personal Development-mode eligibility are required.

## Lifecycle and display

A fresh authenticated Music action owns one request/session on that connection.
The official source is serialized and read no faster than every 3s while at least
one owned view exists, plus a fresh reanchor after a new matched lyric fetch.
The monotonic engine advances the current line between reads, freezes on pause,
handles observed forward/backward seeks, and clears on track/version changes,
missing playback, episodes, ads, disconnect or stale (>5s) clock. Seek/pause changes
are visible after the next API read: this is not sample-accurate playback sync.
Rate limits honor capped Retry-After; source/lyric errors are visible and hide
untrusted text. Finite visible leases cap each view at 300s. Fetches and pollers
cancel when the last owned view closes. Memory-only cache is cleared then.

Reminder alerts, confirmation surfaces, agent results, speaking/listening/thinking,
calendar/tool activity and other scenes stop Music. Resolve the interaction and
explicitly reopen Music. No background scene takeover or automatic reconnect-open.
A per-peer send lock rechecks ownership after waiting; native/browser guards also
reject already-sent old frames by request, fixed session and increasing sequence.
Local dismiss/expiry/replacement/audio activation/reconnect synchronously clears
ownership. Native acceptance and the entire render hold the same recursive BSP
lock used by touch/expiry. Progress-only frames preserve the current lyric scroll.

Frames contain title, artist, progress, playing/paused state, current line, provider
attribution, and touch controls when permitted. Long native lines scroll; combined
glyph-safe label normalization handles missing font glyphs. Original-text browser
screenshots show the current line and all controls at 390px. Physical font/layout
and timing still need device QA after the single approved combined flash.
Older firmware remains compatible with ordinary scenes and receives an explicit
client-update scene for live timing; automatic media frames require
`media_updates_v1`. Firmware and backend must be bundled for the live feature.

## Lyrics and privacy limits

Official Spotify APIs do not provide this lyrics feed. LRCLIB is an explicit
metadata-sharing opt-in, matched on exact normalized title/artist/album and ±2s
duration, bound to the current Spotify recording identity/version. This is a
metadata match, not an authoritative Spotify-to-LRCLIB identifier mapping. The
source card reports LRCLIB record attribution and rights unverified. No private
Spotify endpoints, scraping, guessed lines, plain-lyrics timing, fuzzy search or
paid service is used. Catalogue-wide rights are not established by metadata-sharing consent. Broader
Spotify synchronization terms and lyric rights remain unresolved; an app grant is
not a licensing clearance. Keep this personal; no public SaaS is proposed.

Account-specific grant, song, timing and lyric evidence belong only in private
operator validation records. Verify the user-run grant, a bounded official playback
read, matched timed-document acceptance and current-line selection before deployment.
Synthetic fixtures test the pipeline; they do not establish coverage for every real
recording. Missing recordings show honest no-synced-lyrics; manual/static text does
not satisfy timing. Physical display/clock alignment remains a separate check.

Playback/lyrics stay in the private view memory and wire payload. They never enter
model tools/prompts, Anthropic, speech, jobs, conversation storage or analytics.
Provider exceptions are reduced to fixed statuses. Task-scoped HTTPX/httpcore
filters suppress private request URLs/bodies while retaining unrelated logs.

## Updates and rollback

Preserve private configuration, database and previous firmware before an update.
Keep existing explicit memory and local TLS/device identity settings. Apply the
reviewed source update, validate provider-free tests and native guards, set the
explicit opt-in flags, and deliberately restart the existing service:

```sh
launchctl kickstart -k "gui/$(id -u)/com.friday.backend"
```

Build one firmware image containing both the media ownership guard and glyph-safe
text rendering. Confirm the physical ESP32-P4 port before the approved flash;
retain NVS/Wi-Fi/device identity and never use erase-all. Physical line timing,
readability, dismiss/reentry and user-operated pause/seek remain separate checks.
Private grants, local device trust, recordings and validation artifacts must not
be committed. A normal source checkout does not contain account access.

Rollback backend by setting `MUSIC_PROVIDER=none` and restarting after approval;
use the local disconnect helper if account access must be disabled. Retain the
previous firmware image and configuration for operator-controlled firmware rollback.

## Callback compatibility repair

Synthetic real HTTP listener tests proved normal origin-form callbacks work. The
old parser nevertheless rejected optional OAuth response parameters and exact
loopback absolute-form HTTP targets. The repaired parser accepts the approved
origin/path, ignores bounded unused extra fields, validates an optional Spotify
issuer, and preserves exact Host/state/PKCE checks and duplicate rejection.
This follows [OAuth RFC 6749 section 4.1.2](https://www.rfc-editor.org/rfc/rfc6749#section-4.1.2)
and [HTTP RFC 9112 section 3.2.2](https://www.rfc-editor.org/rfc/rfc9112#section-3.2.2).
Ten actual isolated loopback synthetic cases pass. The user's generic failure does
not identify its real case. New fixed safe diagnostics identify path/state/query
categories without printing any target or values. To deliberately retry, interrupt
the old waiting helper, close old consent/callback tabs, run the same user command,
and use only its fresh consent tab. Never share a callback URL or credential value.
