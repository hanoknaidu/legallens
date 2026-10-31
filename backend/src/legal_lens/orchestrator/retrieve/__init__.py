"""Hybrid retrieval: dense + BM25, fused in Qdrant, reordered by a cross-encoder.

Nothing here imports from the rest of `legal_lens`, and the heavy dependencies
(`torch`, `qdrant-client`, `fastembed`) are imported inside the constructors that need
them — so this package can be imported, and most of it tested, without the
`workflows` extra installed.
"""

from .encode import DenseEncoder, Encoders, Reranker, SparseEncoder
from .index import PassageIndex
from .rerank import covered_pages, rerank
from .retriever import PageStore, Retriever

__all__ = [
    "DenseEncoder",
    "Encoders",
    "PageStore",
    "PassageIndex",
    "Reranker",
    "Retriever",
    "SparseEncoder",
    "covered_pages",
    "rerank",
]
