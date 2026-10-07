"""Per-turn, content-free STT observations; never a shared transcriber state."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
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
        self._phase = Phase.CAPTURE
        self._active = None
        self._durations = {phase: 0 for phase in (Phase.MODEL_LOAD, Phase.INPUT_PREPARE, Phase.DECODE)}

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
        def bounded_us(ns):
            return min(2147483647, max(0, ns // 1000))
        denominator = self.sample_rate * self.channels * 2
        return {"phase": phase, "input_bytes": self.input_bytes,
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
        "transcription_diag reason=%s phase=%s input_bytes=%d sample_rate=%d channels=%d audio_ms=%d total_us=%d model_load_us=%d input_prepare_us=%d decode_us=%d",
        reason.value, metadata["phase"], metadata["input_bytes"], metadata["sample_rate"],
        metadata["channels"], metadata["audio_ms"], metadata["total_us"],
        metadata["model_load_us"], metadata["input_prepare_us"], metadata["decode_us"])
