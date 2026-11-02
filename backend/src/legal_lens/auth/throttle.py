"""Sign-in attempt throttling.

Separate from `middleware.RateLimiter`, which bounds how fast a *signed-in* owner may
use the API. This bounds how many times anyone may guess a password, which is a
different question with a different key: an attacker guessing passwords has no
session to key on, and the per-owner limit would not see them.

Keyed on `email|ip`, so one attacker cannot lock a real user out of their own account
by guessing it from elsewhere — the real user's address-plus-address pair is a
different key. That is the trade: it also means a distributed guesser gets a fresh
allowance per source address, which is what a shared store and a per-account counter
would fix. Noted in `docs/auth.md` (Known limits) rather than pretended away.

In-process, like the rate limiter, and sound for the same reason: the service is
single-worker by design. A second worker gives each its own counters.
"""

from __future__ import annotations

import time
from collections import deque

#: Attempts allowed inside the window before the lockout applies.
MAX_ATTEMPTS = 5
#: How far back failures are counted.
WINDOW_SECONDS = 15 * 60.0
#: How long a key is locked once it trips. Long enough to make guessing uneconomic,
#: short enough that a person who mistyped their password is not calling support.
LOCKOUT_SECONDS = 15 * 60.0


class SignInThrottle:
    def __init__(
        self,
        max_attempts: int = MAX_ATTEMPTS,
        window_seconds: float = WINDOW_SECONDS,
        lockout_seconds: float = LOCKOUT_SECONDS,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._failures: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}

    def check(self, key: str, now: float | None = None) -> int:
        """Seconds the caller must wait, or 0 if the attempt may proceed."""
        if self._max <= 0:
            return 0
        now = now if now is not None else time.monotonic()
        until = self._locked_until.get(key)
        if until is not None:
            if until > now:
                return max(1, int(until - now) + 1)
            # Lapsed. Clear it and the failures that caused it, so the next attempt
            # starts from a clean slate rather than tripping again immediately.
            del self._locked_until[key]
            self._failures.pop(key, None)
        return 0

    def record_failure(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        window = self._failures.setdefault(key, deque())
        cutoff = now - self._window
        while window and window[0] <= cutoff:
            window.popleft()
        window.append(now)
        if len(window) >= self._max:
            self._locked_until[key] = now + self._lockout

    def record_success(self, key: str) -> None:
        """Forget the failures. Someone who eventually remembers their password should
        not stay one typo away from a lockout."""
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
