"""Verifying at startup that the model provider will actually answer.

`/health` used to check only whether an API key was *present*, which made three
distinct failures indistinguishable from a working service: a revoked key, an
exhausted credit balance, and a key that is not scoped to a workspace. All three
authenticate-or-not in different ways, all three were reported as `ok`, and all three
fail every generation at request time.

So this makes one request of one token at startup and remembers what happened. The
cost is a fraction of a cent per boot; the benefit is that "generation is broken" is
visible before a user asks a question rather than after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ..config import Settings

log = logging.getLogger(__name__)

#: The cheapest call that still exercises auth, billing and workspace scoping.
PROBE_MODEL = "claude-haiku-4-5"

Outcome = Literal["ok", "unauthorized", "no_credit", "needs_workspace", "unknown", "unchecked"]


@dataclass(frozen=True)
class ProbeResult:
    outcome: Outcome
    detail: str = ""

    # Deliberately no `healthy` property. Whether the service is degraded is decided
    # in one place — `/health` treats any check not starting with "ok" as a fault —
    # and a second predicate here would have disagreed with it: `unchecked` with no
    # key configured is "unavailable" to the endpoint but looked healthy as an
    # outcome. One source of truth, and it is the string below.

    def as_check(self, settings: Settings) -> str:
        """The string `/health` shows for `llm_provider`."""
        if self.outcome == "unchecked":
            if settings.llm_provider == "echo":
                return "ok (echo stub, no vendor call)"
            if not settings.anthropic_api_key:
                return "no api key: generation unavailable"
            return "ok (key present, not verified)"
        if self.outcome == "ok":
            if settings.llm_provider == "orchestrator":
                # Four models at four tiers, one per workflow, so naming
                # `generation_model` here would name a model this path may never call.
                return "ok (orchestrator workflows)"
            return f"ok ({settings.generation_model})"
        if self.outcome == "unauthorized":
            return "api key rejected: generation unavailable"
        if self.outcome == "no_credit":
            return "no api credit: generation unavailable"
        if self.outcome == "needs_workspace":
            return "api key needs a workspace id: generation unavailable"
        return f"could not verify the api key: {self.detail[:80]}"


UNCHECKED = ProbeResult("unchecked")


async def probe(settings: Settings) -> ProbeResult:
    """Make one minimal request. Never raises."""
    if not settings.probe_llm_on_startup or settings.environment == "test":
        return UNCHECKED
    # Both vendor-calling providers. The probe tests the *key* (it sends a fixed
    # `PROBE_MODEL`), so it is provider-agnostic — and the orchestrator needs it
    # just as much, across four models rather than one.
    if settings.llm_provider not in ("anthropic", "orchestrator"):
        return UNCHECKED
    if not settings.anthropic_api_key:
        return UNCHECKED

    try:
        from anthropic import APIStatusError, AsyncAnthropic
    except ImportError:
        return ProbeResult("unknown", "the anthropic sdk is not installed")

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        default_headers=settings.anthropic_headers or None,
        timeout=settings.probe_timeout_seconds,
        # One attempt: a retry storm at boot delays startup without telling us more.
        max_retries=0,
    )
    try:
        await client.messages.create(
            model=PROBE_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except APIStatusError as exc:
        detail = _message_of(exc)
        outcome = _classify(exc.status_code, detail)
        log.warning(
            "llm probe failed",
            extra={"status": exc.status_code, "outcome": outcome, "detail": detail[:160]},
        )
        return ProbeResult(outcome, detail)
    except Exception as exc:  # noqa: BLE001 — a probe must never stop the service
        # A network blip at boot must not be reported as a bad key: the distinction
        # between "wrong credential" and "could not ask" is the whole point.
        log.warning("llm probe inconclusive", extra={"error_type": type(exc).__name__})
        return ProbeResult("unknown", f"{type(exc).__name__}: {exc}")
    finally:
        await client.close()

    log.info("llm probe ok", extra={"model": settings.generation_model})
    return ProbeResult("ok")


def _message_of(exc) -> str:
    try:
        return exc.response.json().get("error", {}).get("message", "") or str(exc)
    except Exception:
        return str(exc)


def _classify(status: int, detail: str) -> Outcome:
    if status == 401:
        return "unauthorized"
    lowered = detail.lower()
    # Both arrive as 400s and are told apart only by the message, which is why the
    # raw text is kept rather than just the status.
    if "credit balance" in lowered:
        return "no_credit"
    if "workspace" in lowered:
        return "needs_workspace"
    return "unknown"
