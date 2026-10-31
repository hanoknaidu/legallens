"""Guard against a reserved key in a `log.*(..., extra={...})` call.

`logging.Logger.makeRecord` raises `KeyError: "Attempt to overwrite 'filename' in
LogRecord"` for any `extra` key that collides with a built-in record attribute, and it
does so *before* a formatter runs — so `observability._RESERVED`, which filters these
keys at format time, does not protect against it.

The failure mode is what makes this worth a test: the collision only fires when that
log line is reached, which is usually an error path. One of these shipped and turned
every unreadable upload into "an internal error" instead of "this file could not be
read", because the exception handler swallowed the KeyError.
"""

from __future__ import annotations

import logging
import pathlib
import re

RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src"


def test_no_log_extra_uses_a_reserved_record_attribute() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text()
        for match in re.finditer(r"extra=\{([^}]*)\}", text, re.S):
            for key in re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', match.group(1)):
                if key in RESERVED:
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(SOURCE_ROOT)}:{line} key={key!r}")

    assert not offenders, (
        "these `extra` keys collide with LogRecord attributes and will raise "
        "KeyError when the line is reached: " + "; ".join(offenders)
    )


def test_the_collision_really_does_raise() -> None:
    """Pin the behaviour this guard exists for, so the guard is not cargo-culted."""
    logger = logging.getLogger("legal_lens.test.reserved")
    logger.addHandler(logging.NullHandler())

    try:
        logger.warning("boom", extra={"filename": "contract.pdf"})
    except KeyError as exc:
        assert "filename" in str(exc)
    else:
        raise AssertionError("expected logging to reject a reserved `extra` key")

    # The same call with a non-reserved key is fine.
    logger.warning("fine", extra={"file": "contract.pdf"})
