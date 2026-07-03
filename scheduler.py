"""Tiny background scheduler.

Runs periodic jobs (master-data sync, database backup) on a daemon thread so
the container doesn't need an external cron. Deliberately dependency-free: a
single thread wakes on an interval and runs any job whose next-run time has
passed. Jobs must be cheap and self-contained; each runs inside a try/except
so one failure can't kill the loop.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("scheduler")


@dataclass
class Job:
    name: str
    interval_seconds: float
    func: Callable[[], None]
    next_run: float = 0.0
    last_error: str | None = None
    last_run: float | None = None
    runs: int = 0


@dataclass
class BackgroundScheduler:
    tick_seconds: float = 30.0
    jobs: list[Job] = field(default_factory=list)
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _clock: Callable[[], float] = time.monotonic

    def add_job(self, name: str, interval_seconds: float, func: Callable[[], None],
                run_immediately: bool = False) -> None:
        next_run = self._clock() if run_immediately else self._clock() + interval_seconds
        self.jobs.append(Job(name, interval_seconds, func, next_run=next_run))

    def run_due(self, now: float | None = None) -> None:
        """Run every job whose next_run has passed. Exposed for testing."""
        now = self._clock() if now is None else now
        for job in self.jobs:
            if now >= job.next_run:
                try:
                    job.func()
                    job.last_error = None
                except Exception as exc:  # keep the loop alive
                    job.last_error = str(exc)
                    log.warning("scheduled job %r failed: %s", job.name, exc)
                finally:
                    job.last_run = now
                    job.runs += 1
                    job.next_run = now + job.interval_seconds

    def _loop(self) -> None:
        log.info("scheduler started with %d job(s)", len(self.jobs))
        while not self._stop.is_set():
            self.run_due()
            self._stop.wait(self.tick_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.jobs:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def status(self) -> list[dict]:
        return [
            {
                "name": j.name,
                "interval_seconds": j.interval_seconds,
                "runs": j.runs,
                "last_error": j.last_error,
            }
            for j in self.jobs
        ]
