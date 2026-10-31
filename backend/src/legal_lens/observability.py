"""Structured logging and per-request metrics (BRD NFR-13).

Logs are JSON lines so they aggregate without a parser. Every record carries the
request id, which is also returned on the response, so a user-visible failure can be
traced to a log line and — through the audit log — to the generation that produced it.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections import defaultdict
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log duplicates the request middleware below.
    logging.getLogger("uvicorn.access").disabled = True


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:16]}"


class Metrics:
    """In-process metric sink.

    NFR-13 asks for per-request token and cost as metrics. A course project does not
    need a time-series database for that, but it does need the numbers to exist and be
    inspectable, so they are counted here and exposed on GET /metrics. The interface is
    deliberately the shape a StatsD or Prometheus client would present, so swapping the
    sink does not touch a call site.
    """

    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.observations: dict[str, list[float]] = defaultdict(list)

    def incr(self, name: str, value: float = 1.0, **labels: Any) -> None:
        self.counters[self._key(name, labels)] += value

    def observe(self, name: str, value: float, **labels: Any) -> None:
        series = self.observations[self._key(name, labels)]
        series.append(value)
        # Bounded so a long-running dev server does not grow without limit; p95 over
        # the most recent 2,000 observations is what NFR-2 is actually verified against.
        if len(series) > 2000:
            del series[:-2000]

    @staticmethod
    def _key(name: str, labels: dict[str, Any]) -> str:
        if not labels:
            return name
        rendered = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{rendered}}}"

    def snapshot(self) -> dict[str, Any]:
        def percentile(values: list[float], pct: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            # Nearest-rank: index of the first value at or above the percentile.
            idx = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))
            return round(ordered[idx], 4)

        return {
            "counters": {k: round(v, 6) for k, v in sorted(self.counters.items())},
            "latency": {
                name: {
                    "count": len(values),
                    "p50": percentile(values, 50),
                    "p95": percentile(values, 95),
                    "max": round(max(values), 4),
                }
                for name, values in sorted(self.observations.items())
            },
        }


METRICS = Metrics()
