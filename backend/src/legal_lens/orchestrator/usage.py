"""Translating LangChain's token counts into the convention the cost table uses.

The two disagree, and the disagreement is silent.

**Anthropic reports `input_tokens` as the tokens it actually charged input rate for**
— cache reads and cache writes are reported separately and are *not* included in it.
`pricing.cost_usd` adds the four numbers together, so it needs them that way.

**LangChain reports `input_tokens` as the true total**, with cache reads and writes
added back in (`_create_usage_metadata`: *"Anthropic's `input_tokens` excludes cached
tokens, so we manually add ... to get the true total"*). Recording that figure
alongside the cache counts bills every cached token twice — once at the full input
rate and once at the cache rate — which for a prompt that caches well is most of the
bill, inflating the row by roughly 10x on the part that caching was supposed to make
cheap. A cost table that gets *more* expensive when caching starts working is worse
than one with no cache numbers at all.
"""

from __future__ import annotations

from typing import Any

#: Set when a request names a cache TTL explicitly. LangChain then zeroes the generic
#: `cache_creation` key to avoid double counting, and puts the real figure here — so
#: reading only the generic key would report a write of zero on exactly the requests
#: that wrote to the cache.
_TTL_WRITE_KEYS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")


def split_input_tokens(counts: dict[str, Any] | None) -> tuple[int, int, int]:
    """`(fresh_input, cache_read, cache_write)` from LangChain's `usage_metadata`.

    `fresh_input` is floored at zero: it is a subtraction of numbers from the same
    payload, and a negative token count in a cost row is worse than a slightly wrong
    one — it would net off against other rows in a `SUM`.
    """
    counts = counts or {}
    details = counts.get("input_token_details") or {}

    cache_read = int(details.get("cache_read") or 0)
    cache_write = int(details.get("cache_creation") or 0)
    # The TTL-specific keys win when present; see `_TTL_WRITE_KEYS`.
    ttl_write = sum(int(details.get(key) or 0) for key in _TTL_WRITE_KEYS)
    if ttl_write:
        cache_write = ttl_write

    total = int(counts.get("input_tokens") or 0)
    return max(total - cache_read - cache_write, 0), cache_read, cache_write
