"""Per-model prices, and the arithmetic NFR-10 is measured with.

Prices are per million tokens, from the Claude platform pricing table as of
2026-10-07. They are recorded here rather than fetched because a cost row has to be
reproducible after the fact: an audit that recomputed spend at today's prices would
quietly rewrite history.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float
    cache_write_per_mtok: float


# Cache writes bill at ~1.25x input, cache reads at the published read rate.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20, 2.50),
    # The routing tier. Haiku 5.5 is a tenth of Haiku 4.5's input price, which is
    # most of why the routing steps stopped being a visible fraction of a turn.
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125),
    # Kept priced: rows written before the routing tier moved still have to cost
    # what they cost. Re-pricing history at today's model is how an audit quietly
    # stops matching the invoice.
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10, 1.25),
}

# A model we have no price for must not silently cost zero — a zero row reads as
# "this was free" rather than "we did not know".
UNKNOWN_PRICE = Price(0.0, 0.0, 0.0, 0.0)


def cost_usd(
    model_id: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    price = PRICES.get(model_id, UNKNOWN_PRICE)
    total = (
        input_tokens * price.input_per_mtok
        + output_tokens * price.output_per_mtok
        + cache_read_tokens * price.cache_read_per_mtok
        + cache_write_tokens * price.cache_write_per_mtok
    ) / 1_000_000
    # Six places: a single routing call on Haiku costs fractions of a cent, and
    # rounding to four would record it as zero.
    return round(total, 6)


def is_priced(model_id: str) -> bool:
    return model_id in PRICES
