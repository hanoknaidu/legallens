"""Time, in one place and always timezone-aware.

A naive timestamp is the bug in G5 of the integration plan: SQLite stores whatever
string it is given, and `new Date("2026-10-07T12:00:00")` in the browser reads a stamp
with no offset as *local* time, so every date in the UI silently shifts by the
viewer's offset. Everything here is UTC and carries `Z`.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime | None = None) -> str:
    """ISO 8601 UTC with a `Z` suffix, at microsecond precision.

    Microseconds rather than milliseconds because these strings are also the sort key
    for the conversation list: two conversations touched within the same millisecond
    tie, and a recency list with ties has no defined order. `new Date()` in the
    browser truncates the extra digits, so the wire format is unaffected.
    """
    moment = moment or utcnow()
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
