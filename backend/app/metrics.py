"""Minimal Prometheus text exposition without a dependency. Counts only, never content."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
STARTED_AT = time.time()

Sample = tuple[str, dict[str, str], float]


def increment(name: str, amount: float = 1.0, **labels: str) -> None:
    key = (name, tuple(sorted(labels.items())))
    with _lock:
        _counters[key] = _counters.get(key, 0.0) + amount


def counters() -> list[Sample]:
    with _lock:
        return [(name, dict(labels), value) for (name, labels), value in sorted(_counters.items())]


def reset() -> None:
    with _lock:
        _counters.clear()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _line(name: str, labels: dict[str, str], value: float) -> str:
    rendered = ",".join(f'{key}="{_escape(str(val))}"' for key, val in sorted(labels.items()))
    body = f"{name}{{{rendered}}}" if rendered else name
    if float(value).is_integer():
        return f"{body} {int(value)}"
    return f"{body} {value}"


def render(gauges: list[Sample], help_text: dict[str, str] | None = None) -> str:
    """Render gauges plus every recorded counter in Prometheus text format 0.0.4."""
    help_text = help_text or {}
    lines: list[str] = []
    seen: set[str] = set()
    for kind, samples in (("gauge", gauges), ("counter", counters())):
        for name, labels, value in samples:
            if name not in seen:
                seen.add(name)
                if name in help_text:
                    lines.append(f"# HELP {name} {help_text[name]}")
                lines.append(f"# TYPE {name} {kind}")
            lines.append(_line(name, labels, value))
    return "\n".join(lines) + "\n"
