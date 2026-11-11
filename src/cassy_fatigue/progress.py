"""Step-based progress and cancellation for long runs (shared by the CLI and the GUI).

DPF reads cannot report progress from inside one operator call, so a run is counted in
steps (open an analysis, read one set for one material, assess one event, write). The
total is known from the configuration before the reads start. Cancellation is checked at
step boundaries only: a read that has started always finishes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class RunCancelled(RuntimeError):
    """Raised at the next step boundary after :meth:`Progress.cancel`."""


@dataclass(frozen=True)
class Update:
    done: int  # completed steps
    total: int  # planned steps (0 while unknown)
    message: str  # step being worked on, or the last finished one
    elapsed: float  # seconds since the progress object was created
    running: bool  # True when a step has just started, False when it has finished

    def remaining(self) -> float | None:
        """Rough remaining time from the mean step time; ``None`` before the first step."""
        if self.done == 0 or self.total <= self.done:
            return None
        return self.elapsed / self.done * (self.total - self.done)


class Progress:
    def __init__(self, callback: Callable[[Update], None] | None = None):
        self.callback = callback
        self.total = 0
        self.done = 0
        self._start = time.perf_counter()
        self._cancel = threading.Event()

    def plan(self, total: int) -> None:
        """Set (or revise) the planned number of steps."""
        self.total = max(int(total), self.done)

    def cancel(self) -> None:
        """Request cancellation; thread-safe."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise RunCancelled("Run cancelled by the user; no reports were written")

    def _label(self, index: int) -> str:
        return f"[{index}/{self.total}]" if self.total else f"[{index}]"

    def _emit(self, message: str, running: bool) -> None:
        if self.callback is not None:
            self.callback(
                Update(self.done, self.total, message, time.perf_counter() - self._start, running)
            )

    @contextmanager
    def step(self, message: str) -> Iterator[None]:
        self.check()
        index = self.done + 1
        logger.info("%s %s…", self._label(index), message)
        self._emit(message, True)
        start = time.perf_counter()
        yield
        self.done += 1
        self.total = max(self.total, self.done)
        logger.info("%s done in %.1f s", self._label(index), time.perf_counter() - start)
        self._emit(message, False)


def format_seconds(seconds: float) -> str:
    seconds = round(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {seconds:02d} s"
    return f"{seconds} s"
