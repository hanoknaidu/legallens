"""Embedding providers.

The default is deliberately **not** a semantic embedder. `hashing` is a deterministic,
dependency-free stand-in that lets the retrieval pipeline run end to end offline, and
it is honest about what it is: a hashed bag of words with no notion of meaning, so
"terminate" and "end the agreement" are unrelated to it. Lexical scoring in
`select.py` is what actually carries Phase 1 retrieval quality.

A real embedding vendor is a second processor of privileged attorney-client text and
needs its own data-processing agreement before it is switched on (BRD §8.2) — which is
why this is a seam with a safe default rather than a provider to configure later.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from ..config import Settings
from ..lib_text import STOPWORDS, tokens


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Hashed bag-of-words with L2 normalisation.

    Signed hashing (each token contributes +1 or -1 by a second hash bit) so that
    unrelated tokens colliding in the same bucket tend to cancel rather than
    accumulate into a false similarity.
    """

    name = "hashing"

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokens(text):
            if token in STOPWORDS:
                continue
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return vector
        return [v / norm for v in vector]


def embedder_for(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder(settings.embedding_dimensions)
    # `local` and `voyage` are declared in config as the swap targets. Neither is
    # wired yet, and silently falling back would make a configured provider a lie —
    # so this fails loudly at startup rather than mid-request.
    raise NotImplementedError(
        f"embedding_provider={settings.embedding_provider!r} is not implemented; "
        "use 'hashing' until a provider is wired and its DPA is in place (BRD §8.2)"
    )
