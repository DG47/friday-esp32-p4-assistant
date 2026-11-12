# Input and VAD diagnostics

Candidate is based on merged source e7434e23572481e5371c90a00d2a74880b247fbe.
No recognition thresholds, provider arguments or audio behavior are changed.

The existing single failure log now includes PCM status, total sample count,
analyzed sample count, stride, sampled absolute peak/RMS (s16le units), sampled
zero and signed-endpoint clipping proportions in parts per million. At most
12,000 evenly spaced samples are inspected per turn, bounding work on the socket
loop independently of configured clip length. These are sampled estimates for
long inputs: narrow peaks or clipping may be missed, and periodic signals can
alias with the stride. No quiet/silence threshold or speech classification is
applied. All-zero sampled input supports low measured level, not proven silence
across the whole input. Invalid format/alignment reports malformed; zero length
reports empty. Samples, waveforms and transcripts are never retained in metadata.

Installed faster-whisper TranscriptionInfo exposes duration and duration_after_vad.
Its transcribe implementation sets the latter after collecting VAD-selected audio.
Valid enabled-VAD metadata yields all_retained, reduced or none_retained; missing,
non-finite, inconsistent or failing optional metadata yields unknown. Disabled
VAD reports disabled. Duration is milliseconds. A completed segment count is
reported independently; zero is also the default before decoder completion, so
interpret it with the existing phase/reason fields. No segment text, words,
no-speech probability, language or exceptions are logged.

Decoder-empty alone does not imply VAD rejection. none_retained establishes that
available provider metadata retained no audio, not why that happened. Input levels
do not establish speech intelligibility, sample continuity, microphone health or
DMA/network loss. These diagnostics cannot retroactively classify the three
historical empty results. Physical voice testing remains deferred.

Per-turn context and locks keep canceled/late workers isolated. A failure emits
one bounded record; later worker completion does not emit another record or
update another turn. Malformed optional metadata never changes recognized output.
Existing unrelated wake exception-text logging is outside this change.

Deployment requires backend source replacement and restart only. No firmware,
settings, credentials or database changes are required. All testing uses synthetic
PCM, fake models and controlled workers; no real capture or inference.

## Validation

383 aggregate backend tests passed, including seven new metadata tests covering
normal, quiet, zero, empty, malformed, clipped and sampled inputs; VAD outcomes;
hostile optional metadata without recognition changes; bounded private-free logs;
and canceled/late-result isolation. Existing worker lifecycle tests remain passed.
Python compileall and git whitespace checks passed. Independent corrected-diff
review found no blocking issue after addressing full-scan latency and invalid
optional metadata. Five synthetic 60-second/24 kHz scans took 1128–1442 microseconds
on this host; these measurements do not establish live latency.
