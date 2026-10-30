# Mandatory line-by-line lyrics: synthetic timing prototype

Current lyrics are selected line by line from recording-bound timed documents.
Manual/static text does not supply timing. The optional runtime integrates the
engine with official Spotify observations and an explicitly configured provider;
see [spotify-runtime.md](../spotify-runtime.md). Provider-free tests use original text.

`LyricsSyncEngine` is disabled by default and independent of any provider. It
accepts recording-bound `TimedDocument` values and ordered `PlaybackObservation`
updates. Source and attribution accompany the exact track ID/ISRC and version;
duration differences above two seconds refuse the document. Missing recordings,
wrong versions, malformed or duplicate timestamps, missing attribution and
unsupported items fail closed. Request tickets are generation-bound and consumed
once, so superseded, duplicate and previous-track results cannot overwrite the
current document.

Current-line selection uses ordered line timestamps and optional explicit end
boundaries, including instrumental gaps. Playback position interpolates only
while playing, using a monotonic clock. Pause freezes position; resume/seek and
backward seeks replace the observation directly. Positive offsets advance lyrics,
negative offsets delay them; both document and manual adjustment are bounded.
Positions clamp at track duration. Track/version changes clear text immediately
when observed, and reset user offsets. Ads, episodes, errors and missing playback
clear the document. Stale observations hide the current line, including while
paused; fresh updates are required to resume display. Updates arriving out of
sequence are ignored.

A synthetic native-compatible surface shows the selected line as the subtitle,
with explicit synthetic mode, source and attribution. All sample lines were
invented solely for offline QA. No original song lyrics, external provider,
automatic network request, scraping, audio stream or model/history integration
is present. The engine is not routed into Friday's production music scene. The
existing manual binding continues to reject timed documents; it is not presented
as satisfying the user's requirement.

A mocked LRCLIB and Mac AppleScript prototype is now implemented; see
spotify-lyrics-adapters.md. Live source authorization and rights are pending. Existing apps having
line-by-line lyrics does not itself establish a permitted source or Spotify-policy
approval for Friday. This prototype demonstrates timing behavior only. A future
live implementation also needs authorized timed data, approved recording matching,
fresh playback observation delivery, device-visible update/cancellation handling
and native hardware readability QA; none is claimed complete by synthetic tests.
