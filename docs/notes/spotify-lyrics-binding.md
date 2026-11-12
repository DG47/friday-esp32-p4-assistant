# Recording matching for future lyrics

No live lyrics source is configured or authorized in this candidate.
A separately disabled, mocked LRCLIB timing adapter now exists; see
spotify-lyrics-adapters.md. It fetches no
copyrighted lyrics, calls no private Spotify API and enables no lyric timing.
The implemented contract is an identity and provenance boundary for a future
approved provider, tested with original synthetic text only.

`MusicService` normalizes a track into an exact, untruncated Spotify track ID,
optional validated ISRC and recording version metadata (track title, album and
duration). Metadata too large to preserve without truncation cannot form a lyric
identity. Playback display truncation/transliteration does not change this identity.
An ISRC is metadata, not permission to obtain or display lyrics.

`LyricsBinding.observe()` updates this identity before composing the local scene.
A changed track or recording version immediately clears the prior document when
that change is observed. Disabled, disconnected, stale, absent and unsupported
playback also clear it; ads and episodes are unsupported. This is based on observed
playback, not continuous monitoring: with the current conservative touch refresh,
a change between reads is unknown until the next read. There is no claim of instant
Spotify-side change detection or live song/lyrics synchronization.

`load_current()` accepts only the latest in-flight result for the currently bound
recording. Track changes, new requests or an invalid playback observation invalidate
older requests. A provider result must have either the exact track ID or matching
ISRC when no provider track ID is supplied, the exact version metadata, and a
nonempty source and attribution. A conflicting supplied track ID cannot fall back
to matching ISRC. Missing/ambiguous/no-lyrics/error results do not display text.

Accepted documents are labeled **Matched manual text; not time-synced**. The local
structured payload keeps track ID, ISRC, version, source and attribution together
with text. Pause and seek preserve a matching manual document but never infer an
active line or timestamp. Timed documents are refused and labeled disabled, even
if a hypothetical provider supplies timing. Song/lyrics timing would need a
separately authorized source and policy review; it is not promised here.

The default provider returns unavailable. There is no automatic lyrics fetch, file
import, timed cursor, local lyric page route or browser/native lyric-text renderer.
This prevents showing unlicensed content while preserving an implemented matching
boundary. A future manual display can use the structured contract once the user
supplies rights-cleared material or an authorized provider is approved. Lyrics and
Spotify metadata remain outside the agent, model context, jobs and conversation.
