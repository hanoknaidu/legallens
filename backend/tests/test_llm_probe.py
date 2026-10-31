"""The startup probe that tells `/health` whether generation will actually work.

Before this, `/health` reported `llm_provider: ok` whenever a key was *present*. That
made three different failures indistinguishable from a working service, and all three
happened in practice during setup:

  * a revoked key            -> 401
  * an exhausted balance     -> 400 "credit balance is too low"
  * a key with no workspace  -> 400 "must include the anthropic-workspace-id header"

Each one authenticates differently and each one fails every generation. The probe
makes one one-token request at boot and classifies the result, so the operator learns
at startup rather than from a user's failed question.

No test here makes a network call: the probe is skipped under `environment="test"`,
and the classification is tested against the real error payloads directly.
"""

from __future__ import annotations

import pytest

from legal_lens.ai.probe import ProbeResult, _classify, probe
from legal_lens.config import Settings


def _settings(**over) -> Settings:
    base = {
        "environment": "dev",
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-ant-test",
    }
    return Settings(**{**base, **over})


# ---------------------------------------------------------------------------
# Classification — the messages are real ones observed during setup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        (401, "invalid x-api-key", "unauthorized"),
        (
            400,
            "Your credit balance is too low to access the Anthropic API. Please go to "
            "Plans & Billing to upgrade or purchase credits.",
            "no_credit",
        ),
        (
            400,
            "This API key is not scoped to a workspace, so this request must include "
            "the anthropic-workspace-id header with the ID of the workspace to use.",
            "needs_workspace",
        ),
        (400, "something else entirely", "unknown"),
        (500, "internal server error", "unknown"),
    ],
)
def test_real_failures_are_classified(status: int, detail: str, expected: str) -> None:
    # Two of these are both 400s and are told apart only by the message, which is why
    # the raw text is kept rather than just the status code.
    assert _classify(status, detail) == expected


# ---------------------------------------------------------------------------
# What /health shows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "fragment", "degrades"),
    [
        ("ok", "ok (", False),
        ("unauthorized", "api key rejected", True),
        ("no_credit", "no api credit", True),
        ("needs_workspace", "needs a workspace id", True),
        ("unknown", "could not verify", True),
    ],
)
def test_each_outcome_reads_distinctly_and_degrades(
    outcome: str, fragment: str, degrades: bool
) -> None:
    check = ProbeResult(outcome, "boom").as_check(_settings())

    assert fragment in check
    # `/health` decides degradation with one rule: a check must start with "ok".
    assert check.startswith("ok") is not degrades


def test_echo_reports_no_vendor_call() -> None:
    check = ProbeResult("unchecked").as_check(_settings(llm_provider="echo"))
    assert check == "ok (echo stub, no vendor call)"
    assert check.startswith("ok")


def test_a_missing_key_still_degrades_without_a_probe() -> None:
    check = ProbeResult("unchecked").as_check(_settings(anthropic_api_key=None))
    assert check == "no api key: generation unavailable"
    assert not check.startswith("ok")


def test_an_unprobed_key_is_reported_as_unverified_rather_than_working() -> None:
    # Honest about what it does not know: the probe can be switched off, and saying
    # plain "ok" there would reintroduce the exact ambiguity this module removes.
    check = ProbeResult("unchecked").as_check(_settings())
    assert "not verified" in check


# ---------------------------------------------------------------------------
# When the probe runs at all
# ---------------------------------------------------------------------------


async def test_the_probe_is_skipped_in_tests() -> None:
    # Otherwise the suite would make a vendor call per app fixture, which is both a
    # bill and a dependency on the network for every run.
    result = await probe(_settings(environment="test"))
    assert result.outcome == "unchecked"


async def test_the_probe_is_skipped_for_echo() -> None:
    assert (await probe(_settings(llm_provider="echo"))).outcome == "unchecked"


async def test_the_probe_is_skipped_with_no_key() -> None:
    assert (await probe(_settings(anthropic_api_key=None))).outcome == "unchecked"


async def test_the_probe_can_be_switched_off() -> None:
    assert (await probe(_settings(probe_llm_on_startup=False))).outcome == "unchecked"


def test_a_network_failure_is_not_reported_as_a_bad_key() -> None:
    # The distinction between "wrong credential" and "could not ask" is the whole
    # point: a blip at boot must not send someone to rotate a working key.
    check = ProbeResult("unknown", "ConnectError: dns failure").as_check(_settings())
    assert "could not verify" in check
    assert "rejected" not in check
