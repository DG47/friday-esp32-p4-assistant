"""Per-turn, content-free STT observations; never a shared transcriber state."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
import math
import struct
import threading
import time


class FailureReason(str, Enum):
    CAPTURE_MISSING = "capture_missing"
    CAPTURE_MALFORMED = "capture_malformed"
    INPUT_SHORT = "input_short"
    STT_DISABLED = "stt_disabled"
    MODEL_LOAD = "model_load"
    INPUT_PREPARE = "input_prepare"
    DECODE = "decode"
    TIMEOUT = "timeout"
    EMPTY = "empty"
    STT_ERROR = "stt_error"


def public_transcription_message(reason):
    """Device-visible copy is fixed; provider exception bodies are not UI data."""
    return {
        FailureReason.MODEL_LOAD: "Local speech model is unavailable. Check Friday on the Mac.",
        FailureReason.INPUT_PREPARE: "Could not prepare speech audio. Please try again.",
        FailureReason.DECODE: "Local speech decoding failed. Please try again.",
        FailureReason.STT_DISABLED: "Speech transcription is disabled",
        FailureReason.EMPTY: "No speech was recognized",
        FailureReason.TIMEOUT: "Speech transcription timed out",
    }.get(reason, "Speech recognition failed. Please try again.")


class Phase(str, Enum):
    CAPTURE = "capture"
    MODEL_LOAD = "model_load"
    WORKER_WAIT = "worker_wait"
    INPUT_PREPARE = "input_prepare"
    DECODE = "decode"
    RESULT = "result"


class TranscriptionDiagnostics:
    def __init__(self, input_bytes: int, sample_rate: int, channels: int):
        self.input_bytes = input_bytes
        self.sample_rate = sample_rate
        self.channels = channels
        self.started = time.monotonic_ns()
        self._lock = threading.Lock()
        self._audio = {"pcm_status": "unknown", "pcm_samples": 0, "pcm_analyzed_samples": 0, "pcm_stride": 0, "pcm_peak": 0,
                       "pcm_rms": 0, "pcm_zero_ppm": 0, "pcm_clip_ppm": 0,
                       "vad_outcome": "unknown", "vad_input_ms": 0,
                       "vad_retained_ms": 0, "decoder_segments": 0}
        self._phase = Phase.CAPTURE
        self._active = None
        self._durations = {phase: 0 for phase in (Phase.MODEL_LOAD, Phase.INPUT_PREPARE, Phase.DECODE)}

    def input_level(self, pcm, encoding: str) -> None:
        # Sampled s16le magnitudes; no threshold or silence classification implied.
        values = {"pcm_status": "malformed", "pcm_samples": 0, "pcm_analyzed_samples": 0, "pcm_stride": 0, "pcm_peak": 0,
                  "pcm_rms": 0, "pcm_zero_ppm": 0, "pcm_clip_ppm": 0}
        if (encoding == "pcm_s16le" and self.sample_rate > 0 and self.channels > 0
                and len(pcm) % (2 * self.channels) == 0):
            count = len(pcm) // 2
            peak = squares = zeros = clips = 0
            stride = max(1, (count + 11999) // 12000)
            analyzed = 0
            for offset in range(0, len(pcm), stride * 2):
                sample = struct.unpack_from("<h", pcm, offset)[0]
                analyzed += 1
                peak = max(peak, abs(sample))
                squares += sample * sample
                zeros += sample == 0
                clips += sample in (-32768, 32767)
            values.update(pcm_status="valid" if count else "empty", pcm_samples=count,
                          pcm_analyzed_samples=analyzed, pcm_stride=stride if count else 0,
                          pcm_peak=peak, pcm_rms=math.isqrt(squares // analyzed) if analyzed else 0,
                          pcm_zero_ppm=zeros * 1000000 // analyzed if analyzed else 0,
                          pcm_clip_ppm=clips * 1000000 // analyzed if analyzed else 0)
        with self._lock:
            self._audio.update(values)

    def voice_detection(self, enabled: bool, info) -> None:
        values = {"vad_outcome": "unknown" if enabled else "disabled",
                  "vad_input_ms": 0, "vad_retained_ms": 0}
        try:
            before = getattr(info, "duration", None)
            after = getattr(info, "duration_after_vad", None)
            if (enabled and type(before) in (int, float) and type(after) in (int, float)
                    and 0 < before <= 86400 and 0 <= after <= before
                    and math.isfinite(before) and math.isfinite(after)):
                values.update(vad_input_ms=int(before * 1000), vad_retained_ms=int(after * 1000),
                              vad_outcome="none_retained" if after == 0 else
                              "reduced" if after < before else "all_retained")
        except Exception:
            # Optional provider metadata must never change recognition behavior.
            pass
        with self._lock:
            self._audio.update(values)

    def decoded(self, count: int) -> None:
        with self._lock:
            self._audio["decoder_segments"] = min(2147483647, count)

    def phase(self, phase: Phase) -> None:
        with self._lock:
            self._phase = phase

    @contextmanager
    def measure(self, phase: Phase):
        started = time.monotonic_ns()
        with self._lock:
            self._phase = phase
            self._active = (phase, started)
        try:
            yield
        finally:
            with self._lock:
                self._durations[phase] += max(0, time.monotonic_ns() - started)
                self._active = None

    def snapshot(self) -> dict[str, int | str]:
        # The executor thread may still run after wait_for cancellation. Snapshot
        # under a short lock; it never logs and never touches the next turn.
        with self._lock:
            now = time.monotonic_ns()
            durations = self._durations.copy()
            if self._active is not None:
                phase, started = self._active
                durations[phase] += max(0, now - started)
            phase = self._phase.value
            audio = self._audio.copy()
        def bounded_us(ns):
            return min(2147483647, max(0, ns // 1000))
        denominator = self.sample_rate * self.channels * 2
        return {**audio, "phase": phase, "input_bytes": self.input_bytes,
                "sample_rate": self.sample_rate, "channels": self.channels,
                "audio_ms": self.input_bytes * 1000 // denominator if denominator else 0,
                "total_us": bounded_us(now - self.started),
                **{p.value + "_us": bounded_us(n) for p, n in durations.items()}}


_current: ContextVar[TranscriptionDiagnostics | None] = ContextVar("stt_diagnostics", default=None)


@contextmanager
def observe(diagnostics: TranscriptionDiagnostics):
    token = _current.set(diagnostics)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def measure(phase: Phase):
    diagnostics = _current.get()
    if diagnostics is None:
        yield
    else:
        with diagnostics.measure(phase):
            yield


def phase(value: Phase) -> None:
    diagnostics = _current.get()
    if diagnostics is not None:
        diagnostics.phase(value)


def log_failure(logger, diagnostics: TranscriptionDiagnostics, reason: FailureReason) -> None:
    reason = reason if isinstance(reason, FailureReason) else FailureReason.STT_ERROR
    metadata = diagnostics.snapshot()
    logger.warning(
        "transcription_diag reason=%s phase=%s input_bytes=%d sample_rate=%d channels=%d audio_ms=%d total_us=%d model_load_us=%d input_prepare_us=%d decode_us=%d pcm_status=%s pcm_samples=%d pcm_analyzed_samples=%d pcm_stride=%d pcm_peak=%d pcm_rms=%d pcm_zero_ppm=%d pcm_clip_ppm=%d vad_outcome=%s vad_input_ms=%d vad_retained_ms=%d decoder_segments=%d",
        reason.value, metadata["phase"], metadata["input_bytes"], metadata["sample_rate"],
        metadata["channels"], metadata["audio_ms"], metadata["total_us"],
        metadata["model_load_us"], metadata["input_prepare_us"], metadata["decode_us"],
        *[metadata[k] for k in ("pcm_status", "pcm_samples", "pcm_analyzed_samples", "pcm_stride", "pcm_peak", "pcm_rms",
                               "pcm_zero_ppm", "pcm_clip_ppm", "vad_outcome", "vad_input_ms",
                               "vad_retained_ms", "decoder_segments")])


def input_level(pcm, encoding: str) -> None:
    diagnostics = _current.get()
    if diagnostics is not None:
        diagnostics.input_level(pcm, encoding)


def voice_detection(enabled: bool, info) -> None:
    diagnostics = _current.get()
    if diagnostics is not None:
        diagnostics.voice_detection(enabled, info)


def decoded(count: int) -> None:
    diagnostics = _current.get()
    if diagnostics is not None:
        diagnostics.decoded(count)
