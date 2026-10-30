# Bounded line-by-line provider prototype

The optional official Spotify runtime is documented in
[spotify-runtime.md](../spotify-runtime.md). Automatic tests use original synthetic
metadata and lyrics. Live lyric lookups require explicit metadata-sharing opt-in;
account grants do not establish lyric rights. Spotify synchronization terms remain
a separate contractual question.

## LRCLIB adapter

The opt-in `LRCLIBAdapter` uses an injected HTTP client and
`https://lrclib.net/api/get`, requesting track_name, artist_name, album_name and
duration in seconds. It requires a configured client-identification User-Agent.
Names match strictly after Unicode/space/case normalization; remixes, albums,
artists or durations differing by more than two seconds are rejected. It performs
no search/fuzzy fallback and does not manufacture a Spotify ID from LRCLIB data:
returned metadata is bound to the requested recording and labeled metadata-matched,
with the LRCLIB record ID and rights-unverified attribution.

Only syncedLyrics is accepted. Plain text and instrumental/not-found results
produce **no synced lyrics**, never a static substitute. The LRC parser handles
fractional timestamps, multiple timestamps/repeated lines, simultaneous distinct
text, blank boundaries/instrumental gaps and bounded offsets. It strips optional
word timing tags because this prototype implements current lines, not word fill.
Malformed timings and conflicting offsets fail closed.

Requests serialize under a lock, use a bounded 32-record memory cache, refuse
redirects and bound body size/timeouts. Negative results are cached too. Rate
limits honor a finite bounded Retry-After and transient failures impose backoff.
No polling or automatic song requests run by default. `TimedLyricsSession` starts
a fetch only on an observed recording change; pause/seek updates do not refetch.
Generation tickets prevent late/duplicate responses from repopulating another
recording. An injected fresh playback read can reanchor the cursor after fetch
latency; later observations also correct interpolation drift. No persistent lyric
cache, external model processing or background live-service integration exists.

## Mac and Web API playback contracts

`MacSpotifyAdapter` reads via an explicitly injected runner. The candidate's
constant script performs no playback writes and checks Spotify is running rather
than launching it. It verifies track identity is unchanged across the read,
checks application version, rejects episodes/ads/unknown IDs, and produces the
same observation/query contract as the Web API snapshot conversion. Process
output/timeouts are bounded; errors contain no metadata. Default configuration
executes no process or Automation prompt.

Read-only inspection of the installed files found Spotify 1.3.1.234 and its
Spotify.sdef fields for current track, ID, title, artist, album, duration, player
position and player state. No AppleScript was executed or compiled. The dictionary
calls duration seconds, while the candidate deliberately permits separately
verified seconds/milliseconds conversion. Actual returned units and script output
need one approved bounded local read before reliance. Unknown units/version fail
closed. The Mac route requires Spotify desktop running plus explicit macOS
Automation permission for the chosen host process. It does not need Spotify OAuth
for local reads; phone/Connect playback uses the separate approved Web API path.
Neither source is selected or activated yet. Web API snapshots preserve full
metadata for matching separately from display truncation.

## Remaining permissions and verification

1. Choose the playback source/device. No automatic device transfer or playback
   change is implemented.
2. For Mac reads, separately approve Automation/local Spotify metadata access;
   verify the actual installed script output, version and duration units. No
   Accessibility, microphone or speech-recognition permission is requested.
3. For LRCLIB, separately approve sending track/artist/album/duration metadata to
   that external service and choose a truthful app identification contact/link.
   Its free/no-key API does not itself settle lyric-use rights or Spotify terms.
4. Review the policy/rights boundary before live timed display. Complete source
   freshness, device-visible update/cancellation delivery, Unicode/font and
   physical readability QA before enabling a service. Runtime feature gates stay
   disabled; no live connection/deployment/flash/push occurred.

Primary references reviewed (no project code copied):
- https://lrclib.net/docs
- https://github.com/denyherianto/SpotifyLyrics (MIT personal Mac app example)

All parser, HTTP, process and timing tests use original invented sample text and
mocked transports/process outputs. The example proves practical feasibility, not
approval or rights for Friday.

Verification:284 backend tests pass. Synthetic adapter coverage includes 20 tests
for disabled zero-I/O gates, exact query/header/matching, no-static fallback, LRC
repeats/blanks/offsets, HTTP/backoff/bounds, mocked process/version/units/timeouts,
fetch-latency reanchoring, late metadata and stale generation results. The actual
simulator also passed original-line transitions at1200/3200/7200ms and a backward
seek to1200ms. Narrow screenshots are in outputs/spotify/sync-*-390.png and
assertion results in browser-qa.json. Physical rendering remains unverified.
