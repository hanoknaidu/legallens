"""Stem matching and passage scoring.

These exist because the scorer's two bugs were both silent: a one-way prefix test
made an indemnity question miss the indemnity clause entirely, and an unguarded
reverse test made the word "in" match "indemnification" and score a governing-law
clause as a hit. Neither raised anything — the first abstained, the second cited the
wrong clause, and both look like model quality problems rather than a scoring bug.
"""

from __future__ import annotations

import pytest

from legal_lens.lib_text import (
    best_sentences,
    keywords,
    score_text,
    sentences,
    stem_match,
)

INDEMNITY = (
    "The Supplier shall indemnify the Customer against all third-party claims "
    "arising from the Services."
)
GOVERNING = (
    "This Agreement is governed by the laws of Delaware, and venue lies exclusively "
    "in New Castle County."
)


@pytest.mark.parametrize(
    ("word", "term"),
    [
        # The pair that matters most: neither is a prefix of the other, so only a
        # shared-stem test pairs them.
        ("indemnify", "indemnification"),
        ("indemnification", "indemnify"),
        ("termination", "terminate"),
        ("terminate", "termination"),
        ("payment", "pay"),
        ("governing", "govern"),
        ("insurance", "insurance"),
    ],
)
def test_same_root_words_match(word: str, term: str) -> None:
    assert stem_match(word, term) is True


@pytest.mark.parametrize(
    ("word", "term"),
    [
        # A short document word must not match a long query term just by being its
        # prefix — this is what cited a governing-law clause on an indemnity question.
        ("in", "indemnification"),
        ("is", "insurance"),
        ("a", "agreement"),
        ("to", "termination"),
        ("the", "term"),
        # Different roots that happen to share three letters.
        ("index", "indemnify"),
        ("party", "payment"),
    ],
)
def test_unrelated_words_do_not_match(word: str, term: str) -> None:
    assert stem_match(word, term) is False


def test_an_indemnity_question_scores_the_indemnity_clause_and_nothing_else() -> None:
    terms = keywords("What are the indemnification obligations?")

    assert score_text(INDEMNITY, terms) > 0
    # Exactly zero, not merely lower: the governing-law clause has no bearing on the
    # question, and a nonzero score would put it in the citation list.
    assert score_text(GOVERNING, terms) == 0.0


def test_coverage_beats_repetition() -> None:
    terms = keywords("termination notice")
    covers_both = "Termination requires written notice."
    repeats_one = "Termination, termination, termination, termination."

    assert score_text(covers_both, terms) > score_text(repeats_one, terms)


def test_stopwords_cannot_carry_a_match() -> None:
    # Without stopword filtering, any sentence containing "the" scored against a
    # question containing "the".
    assert score_text("The of and to in on at by.", keywords("the agreement")) == 0.0


def test_question_words_are_not_search_terms() -> None:
    assert keywords("What are the indemnification obligations?") == [
        "indemnification",
        "obligations",
    ]


# ---------------------------------------------------------------------------
# Sentence splitting — citations are located by substring, so this is exact work
# ---------------------------------------------------------------------------


def test_clause_numbers_and_abbreviations_do_not_split_sentences() -> None:
    text = (
        "Section 7.2 Indemnification. The Supplier shall indemnify Arbor Systems, "
        "Inc. against claims. Venue lies in Delaware."
    )
    result = sentences(text)

    assert "The Supplier shall indemnify Arbor Systems, Inc. against claims." in result
    # "7.2" must not have become two sentences.
    assert not any(s.strip() in ("2 Indemnification.", "7.") for s in result)


def test_sentences_are_returned_verbatim() -> None:
    # Citation highlighting finds `cited_text` inside the stored page text, so a
    # splitter that normalised quotes or collapsed whitespace would produce citations
    # that cannot be found where they claim to be.
    text = 'The term "Confidential  Information" means  any non-public data.'
    assert sentences(text)[0] == text


def test_best_sentences_returns_one_per_page_and_quotes_verbatim() -> None:
    pages = [
        ("d-1", "MSA", 2, INDEMNITY),
        ("d-1", "MSA", 3, GOVERNING),
    ]
    found = best_sentences(pages, keywords("indemnify claims"), limit=3)

    assert len(found) == 1
    document_id, title, page, sentence = found[0]
    assert (document_id, title, page) == ("d-1", "MSA", 2)
    # Verbatim, and findable in the page it names.
    assert sentence in INDEMNITY
