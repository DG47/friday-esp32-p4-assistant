"""Operator alerts: always logged, optionally handed to a local command, rate limited per kind."""
from __future__ import annotations

import asyncio
import logging
import shlex
import time
from typing import Callable

from . import metrics

logger = logging.getLogger(__name__)


class AlertSink:
    def __init__(
        self,
        command: str = "",
        cooldown_seconds: float = 3600.0,
        timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.command = shlex.split(command) if command.strip() else []
        self.cooldown = max(0.0, cooldown_seconds)
        self.timeout = max(1.0, timeout_seconds)
        self.clock = clock
        self._last_sent: dict[str, float] = {}

    async def notify(self, kind: str, message: str) -> bool:
        """Log the alert and run the configured command. Returns True when the command ran."""
        logger.error("Friday alert %s: %s", kind, message)
        metrics.increment("friday_alerts_total", kind=kind)
        if not self.command:
            return False
        now = self.clock()
        last = self._last_sent.get(kind)
        if last is not None and now - last < self.cooldown:
            return False
        self._last_sent[kind] = now
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command, kind, message,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Alert command failed for %s: %s", kind, type(error).__name__)
            return False
        return True


async def watch_tasks(tasks: list[asyncio.Task], sink: AlertSink, interval_seconds: float = 30.0) -> None:
    """Alert once for every background task that stops; readiness reports the same condition."""
    reported: set[str] = set()
    while True:
        for task in list(tasks):
            name = task.get_name()
            if task.done() and name not in reported and task is not asyncio.current_task():
                reported.add(name)
                await sink.notify("worker_stopped", f"Background task {name} stopped")
        await asyncio.sleep(max(0.01, interval_seconds))
