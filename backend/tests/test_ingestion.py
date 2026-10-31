"""Parsing, chunking and the FR-2 rejection.

These test the pipeline directly rather than through HTTP, because what matters here
is the verdict and the text — the status codes are covered in the API tests.
"""

from __future__ import annotations

import pytest

from fixtures import docx_file, scanned_pdf, text_pdf, txt_file
from legal_lens.config import Settings
from legal_lens.ingestion.chunk import chunk_pages
from legal_lens.ingestion.constants import (
    NO_TEXT_LAYER_REASON,
    detect_kind,
    is_accepted_filename,
    title_from_filename,
)
from legal_lens.ingestion.parse import UnreadableFile, parse

PARSE_ARGS = {"min_chars_per_text_page": 24, "page_budget_chars": 2800}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_pdf_pages_keep_their_own_numbering_and_text() -> None:
    data = text_pdf(
        [
            "Section 7.2 Indemnification. The Supplier shall indemnify the Customer.",
            "Section 9.1 Governing Law. This Agreement is governed by Delaware law.",
        ]
    )
    result = parse(data, ".pdf", **PARSE_ARGS)

    assert result.page_count == 2
    assert result.empty_pages == 0
    assert [p.page for p in result.pages] == [1, 2]
    assert "Indemnification" in result.pages[0].text
    assert "Delaware" in result.pages[1].text


def test_a_scanned_pdf_yields_no_text_layer() -> None:
    result = parse(scanned_pdf(3), ".pdf", **PARSE_ARGS)

    # Every page was reached and every page was empty. That pair is the FR-2 condition:
    # not "the file failed to parse", but "it parsed and there was nothing in it".
    assert result.page_count == 3
    assert result.empty_pages == 3
    assert result.total_chars == 0


def test_docx_tables_are_not_dropped() -> None:
    data = docx_file(
        ["Payment terms follow."],
        table=[["Milestone", "Fee"], ["Delivery", "USD 40,000"]],
    )
    result = parse(data, ".docx", **PARSE_ARGS)
    text = "\n".join(p.text for p in result.pages)

    # Fee tables are exactly what gets asked about, and a parser that walked only
    # paragraphs would silently lose the numbers.
    assert "USD 40,000" in text
    assert "Milestone" in text


def test_txt_is_decoded_and_paginated() -> None:
    result = parse(txt_file("alpha\nbeta\ngamma"), ".txt", **PARSE_ARGS)
    assert result.page_count == 1
    assert "beta" in result.pages[0].text


def test_long_text_breaks_into_pages_at_block_boundaries() -> None:
    # Each paragraph is well under the budget, so no page should split one.
    paragraph = "x" * 500
    result = parse(txt_file("\n".join([paragraph] * 20)), ".txt", **PARSE_ARGS)

    assert result.page_count > 1
    for page in result.pages:
        for line in page.text.split("\n"):
            assert line == paragraph or line == ""


def test_unreadable_bytes_raise_rather_than_returning_empty() -> None:
    # Distinguishable from a scan: a scan parses to empty pages, whereas this cannot
    # be parsed at all, and the two get different messages.
    with pytest.raises(UnreadableFile):
        parse(b"this is not a pdf at all", ".pdf", **PARSE_ARGS)


def test_text_is_preserved_verbatim_for_citation_highlighting() -> None:
    # The whole citation mechanism rests on this: the viewer finds `citedText` inside
    # the stored page text. Any normalisation here breaks highlighting silently.
    span = "the Supplier shall maintain commercial general liability insurance"
    data = text_pdf([f"Section 12.1 Insurance. {span}, with limits."])
    result = parse(data, ".pdf", **PARSE_ARGS)
    assert span in result.pages[0].text


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_chunks_record_the_pages_they_came_from() -> None:
    result = parse(
        text_pdf([f"Page {i} content. " + "y" * 400 for i in range(1, 9)]), ".pdf", **PARSE_ARGS
    )
    chunks = chunk_pages(result.pages, target_chars=800, overlap_chars=200)

    assert chunks
    for chunk in chunks:
        # A chunk that could not name its pages could only be cited as "somewhere in
        # the document", which is not a citation.
        assert 1 <= chunk.page_start <= chunk.page_end <= result.page_count
        assert chunk.text


def test_chunks_overlap_so_a_split_clause_survives_whole() -> None:
    result = parse(
        text_pdf([f"Clause {i}. " + "z" * 400 for i in range(1, 7)]), ".pdf", **PARSE_ARGS
    )
    chunks = chunk_pages(result.pages, target_chars=800, overlap_chars=400)

    assert len(chunks) > 1
    # Consecutive chunks share at least one page, which is what keeps a clause that
    # straddles a boundary intact in one of them.
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert later.page_start <= earlier.page_end


def test_defined_terms_are_captured() -> None:
    result = parse(
        text_pdf(['1.1 Definitions. "Confidential Information" means any non-public data.']),
        ".pdf",
        **PARSE_ARGS,
    )
    chunks = chunk_pages(result.pages, target_chars=2800, overlap_chars=240)
    assert any("Confidential Information" in c.defined_terms for c in chunks)


def test_a_document_with_no_text_produces_no_chunks() -> None:
    result = parse(scanned_pdf(2), ".pdf", **PARSE_ARGS)
    assert chunk_pages(result.pages, target_chars=2800, overlap_chars=240) == []


# ---------------------------------------------------------------------------
# Upload rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "accepted"),
    [
        ("contract.pdf", True),
        ("MEMO.DOCX", True),
        ("notes.txt", True),
        ("scan.png", False),
        ("archive.zip", False),
        ("noextension", False),
    ],
)
def test_accepted_file_types_match_the_client_gate(filename: str, accepted: bool) -> None:
    assert is_accepted_filename(filename) is accepted


@pytest.mark.parametrize(
    ("filename", "kind"),
    [
        ("northwind-arbor-msa-executed.pdf", "msa"),
        ("mutual-nda.pdf", "nda"),
        ("calder-v-brightpoint-complaint.pdf", "complaint"),
        ("random-notes.txt", "other"),
    ],
)
def test_kind_is_guessed_from_the_filename(filename: str, kind: str) -> None:
    assert detect_kind(filename) == kind


def test_title_mirrors_the_mock_wording() -> None:
    assert title_from_filename("northwind-arbor_msa.pdf") == "northwind arbor msa"


def test_the_rejection_reason_is_the_text_the_frontend_renders() -> None:
    # RejectedScanNotice.tsx carries this same string as its fallback. If the two
    # drift, a user sees one explanation from the server and another from the client.
    assert NO_TEXT_LAYER_REASON.startswith("This PDF has no extractable text layer")
    assert "Optical character recognition is not available" in NO_TEXT_LAYER_REASON


def test_settings_defaults_are_what_the_pipeline_assumes() -> None:
    settings = Settings()
    assert settings.min_chars_per_text_page == 24
    assert settings.max_pages == 500
    assert settings.max_upload_bytes == 50 * 1024 * 1024
