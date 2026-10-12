"""Client-side request pacing shared by every thread using one API client.

:class:`RequestRateLimiter` spaces request *starts* at least ``1 / max_per_second``
apart. A caller takes a slot under a lock only when it can start *now*; otherwise it
sleeps outside the lock until its earliest possible start and checks again. Callers
never busy-wait, a single thread (inline / no-threads mode) simply sleeps between
its own requests, and a pause that begins while a caller is asleep still holds it.

:meth:`RequestRateLimiter.pause` is called when the server pushes back (429, or a
503 with ``Retry-After``). Until the pause ends, every *other* caller's next slot
waits for it, plus a small random jitter, so parallel workers don't all resume at
the same instant. The pausing caller is exempt: it already sleeps through its own
retry backoff.

Pacing can be disabled (``max_per_second`` of ``None`` or ``0``). Pauses still
apply then. The clock, sleep and randomness are injectable so tests are
deterministic.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

# Bound at import so tests that monkeypatch ``time.sleep`` / ``time.monotonic`` for
# retry assertions don't also capture (or fake) the limiter's own waiting.
_MONOTONIC = time.monotonic
_SLEEP = time.sleep

#: Jitter added to a pause's end for waiting callers: a fraction of the pause, capped.
PAUSE_JITTER_FRACTION = 0.2
PAUSE_JITTER_MAX_SECONDS = 2.0


class RequestRateLimiter:
    """Thread-safe minimum-interval limiter with a shared server-pushback pause."""

    def __init__(
        self,
        max_per_second: float | None,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        rand: Callable[[], float] | None = None,
        thread_id: Callable[[], int] | None = None,
    ) -> None:
        self._interval = self._interval_for(max_per_second)
        self._clock = clock or _MONOTONIC
        self._sleep = sleep or _SLEEP
        self._rand = rand or random.random
        self._thread_id = thread_id or threading.get_ident
        self._lock = threading.Lock()
        self._last_start: float | None = None  # next slot = last start + current interval
        self._paused_until = 0.0
        self._pause_owner: int | None = None

    @staticmethod
    def _interval_for(max_per_second: float | None) -> float:
        return 1.0 / max_per_second if max_per_second and max_per_second > 0 else 0.0

    @property
    def max_per_second(self) -> float | None:
        return 1.0 / self._interval if self._interval else None

    def set_rate(self, max_per_second: float | None) -> None:
        """Change the pacing rate in place; an active pause and its owner are kept.

        The next slot is derived from the last start and the *new* interval, so
        lowering the rate takes effect for the very next request.
        """

        with self._lock:
            self._interval = self._interval_for(max_per_second)

    def acquire(self, max_wait: float | None = None) -> float | None:
        """Wait for this caller's request slot; return the seconds waited.

        The slot is taken only once the caller can start immediately, after
        re-checking any pause that began (or was extended) while it slept. With
        *max_wait* (seconds, relative, so independent of the caller's clock), a
        slot that cannot start within it in total is refused before sleeping:
        ``None`` is returned and no slot is taken.
        """

        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                start = now
                if self._interval and self._last_start is not None:
                    start = max(now, self._last_start + self._interval)
                if self._paused_until > now and self._thread_id() != self._pause_owner:
                    remaining = self._paused_until - now
                    jitter = min(PAUSE_JITTER_MAX_SECONDS, PAUSE_JITTER_FRACTION * remaining) * self._rand()
                    start = max(start, self._paused_until + jitter)
                if start <= now:
                    self._last_start = now
                    return waited
                if max_wait is not None and waited + (start - now) > max_wait:
                    return None
                wait = start - now
            self._sleep(wait)
            waited += wait

    def pause(self, seconds: float) -> None:
        """Hold every other caller's requests for *seconds* (server asked us to back off)."""

        if seconds <= 0:
            return
        with self._lock:
            until = self._clock() + seconds
            if until > self._paused_until:
                self._paused_until = until
                self._pause_owner = self._thread_id()


__all__ = ["PAUSE_JITTER_FRACTION", "PAUSE_JITTER_MAX_SECONDS", "RequestRateLimiter"]
