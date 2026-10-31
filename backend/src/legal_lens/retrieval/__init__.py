"""Choosing what the model is shown.

Two strategies, selected per request by size (BRD §4.2). Below
`whole_document_token_budget` the entire corpus in scope goes into context: nothing
can be missed, and questions about *absence* become answerable, which retrieval cannot
do — "the contract is silent on insurance" is only provable if the whole contract was
read. Above the budget, RAG earns its place.
"""

from .embeddings import HashingEmbedder, embedder_for
from .select import Selection, select_pages
from .store import MemoryVectorStore

__all__ = [
    "HashingEmbedder",
    "MemoryVectorStore",
    "Selection",
    "embedder_for",
    "select_pages",
]
