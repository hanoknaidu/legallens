"""Sentence splitting, tokenising and keyword scoring.

Shared by the echo provider and by retrieval. Kept dependency-free and deterministic:
the same question against the same documents has to produce the same answer, or the
audit log records something that cannot be reproduced.
"""

from __future__ import annotations

import math
import re

# Legal prose is full of abbreviations and numbered clauses, so a naive split on
# `[.!?]` breaks "Section 7.2." and "Inc." into fragments. This requires the
# terminator to be followed by whitespace and a capital or digit, and protects the
# abbreviations that actually occur in these documents.
_ABBREVIATIONS = (
    "Inc",
    "LLC",
    "Ltd",
    "Corp",
    "Co",
    "No",
    "Nos",
    "Art",
    "Sec",
    "Mr",
    "Mrs",
    "Ms",
    "Dr",
    "Jr",
    "Sr",
    "v",
    "vs",
    "et al",
    "e.g",
    "i.e",
    "U.S",
    "U.S.C",
)
_PROTECT = re.compile(r"\b(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.", re.I)
_DECIMAL = re.compile(r"(\d)\.(\d)")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9\"“(])")

_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Words that carry no retrieval signal. Short, because an aggressive list removes
# terms that matter in contracts ("notice", "term", "party" are all meaningful).
STOPWORDS = frozenset(
    """a an and are as at be been but by can do does for from had has have how i if in
    into is it its may me my no nor not of on or our shall should so than that the
    their them then there these they this to under was we were what when where which
    who will with would you your about does did doing done""".split()
)

_SENTINEL = "\x00"

# Words that tell the system *what to do* rather than what to look for. "Summarise
# the key terms" must not search the documents for "summarise" — those words are the
# instruction, and leaving them in made a summary request abstain because no contract
# contains the word "summarise".
INSTRUCTION_WORDS = frozenset(
    """summarise summarize summarised summarized summary summarising summarizing
    draft drafting memo memorandum prepare write overview outline brief
    key main list give show tell explain describe""".split()
)


def sentences(text: str) -> list[str]:
    """Split into sentences without altering the characters inside them.

    Verbatim matters: a citation's `cited_text` is located by substring search in the
    stored page text, so a splitter that normalised quotes or collapsed whitespace
    would produce citations that cannot be found where they say they are.
    """
    if not text or not text.strip():
        return []

    protected = _PROTECT.sub(lambda m: m.group(0).replace(".", _SENTINEL), text)
    protected = _DECIMAL.sub(lambda m: f"{m.group(1)}{_SENTINEL}{m.group(2)}", protected)

    parts = []
    for raw in _SENTENCE_END.split(protected):
        restored = raw.replace(_SENTINEL, ".").strip()
        if restored:
            parts.append(restored)
    return parts


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def keywords(text: str, *, drop_instructions: bool = False) -> list[str]:
    """Content words from a question, in order, de-duplicated.

    With `drop_instructions`, words naming the task are removed as well. Use that
    wherever the terms are about to be matched against document text.
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens(text):
        if token in STOPWORDS or len(token) < 2 or token in seen:
            continue
        if drop_instructions and token in INSTRUCTION_WORDS:
            continue
        seen.add(token)
        out.append(token)
    return out


def search_terms(text: str) -> list[str]:
    """The terms to retrieve with: content words, minus the instruction."""
    return keywords(text, drop_instructions=True)


#: Shortest shared prefix that counts as the same stem. Six keeps "terminate" and
#: "termination" together while keeping "indemnify" away from "index".
STEM_MIN = 6


def stem_match(word: str, term: str) -> bool:
    """Whether a document word and a question term are the same root.

    Matching runs in both directions, but asymmetrically, and the asymmetry is the
    point. Forward (`word.startswith(term)`) is safe at any length because the term
    is what the user typed: someone searching "pay" means to find "payment".
    Backward is not — an unguarded `term.startswith(word)` lets the two-letter word
    "in" match "indemnification", which scored a governing-law clause as a hit on an
    indemnity question. So the backward direction requires a substantial word, and
    everything else goes through the shared-prefix test, which is what actually pairs
    "indemnify" with "indemnification".
    """
    if word == term or word.startswith(term):
        return True
    if len(word) >= STEM_MIN and term.startswith(word):
        return True
    if min(len(word), len(term)) < STEM_MIN:
        return False
    return word[:STEM_MIN] == term[:STEM_MIN]


def score_text(text: str, terms: list[str]) -> float:
    """How well a passage matches the terms."""
    if not terms:
        return 0.0
    # Stopwords are dropped from the passage as well as the query. Leaving them in
    # gives every sentence a chance to match on a filler word and inflates the
    # density denominator with words that carry no signal.
    haystack = [t for t in tokens(text) if t not in STOPWORDS]
    if not haystack:
        return 0.0

    hits = 0.0
    matched_terms = 0
    for term in terms:
        term_hits = sum(1 for word in haystack if stem_match(word, term))
        if term_hits:
            matched_terms += 1
            hits += term_hits
    if not hits:
        return 0.0

    # Coverage dominates frequency: a passage mentioning both of the question's terms
    # once each is a better answer than one repeating a single term four times.
    #
    # Frequency is log-damped for that to actually hold. With raw `hits` the two
    # score identically — the repetition's density gain exactly cancels its coverage
    # loss — so the tenth mention of one term has to be worth far less than the
    # first mention of a second term.
    coverage = matched_terms / len(terms)
    density = math.log1p(hits) / (len(haystack) ** 0.5)
    return coverage * 3.0 + density


def best_sentences(
    pages: list[tuple[str, str, int, str]], terms: list[str], limit: int = 3
) -> list[tuple[str, str, int, str]]:
    """Pick the sentences that best answer `terms`, at most one per page.

    Returns `(document_id, document_title, page, sentence)`. One per page because
    three citations to the same paragraph tell the reader nothing the first did not.
    """
    scored: list[tuple[float, tuple[str, str, int, str]]] = []
    for document_id, title, page, text in pages:
        best: tuple[float, str] | None = None
        for sentence in sentences(text):
            # Very short fragments make poor citations even when they match.
            if len(sentence) < 40:
                continue
            score = score_text(sentence, terms)
            if score > 0 and (best is None or score > best[0]):
                best = (score, sentence)
        if best is not None:
            scored.append((best[0], (document_id, title, page, best[1])))

    scored.sort(key=lambda row: (-row[0], row[1][0], row[1][2]))
    return [payload for _, payload in scored[:limit]]
