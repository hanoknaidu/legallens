"""Export (FR-17, FR-18) and the audit log (FR-20, NFR-9).

FR-17 is the assertion that matters most here, and it is the one easy to fake: the
unreviewed-draft notice has to be **inside the exported file**, not only in the UI.
The file is what gets emailed, and by then the banner is gone.
"""

from __future__ import annotations

import io
import json
from datetime import datetime

from httpx import AsyncClient

from helpers import finished_turn


def _docx_text(content: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(content))
    return "\n".join(p.text for p in document.paragraphs)


# ---------------------------------------------------------------------------
# FR-17: the notice travels with the file
# ---------------------------------------------------------------------------


async def test_the_markdown_export_carries_the_notice_inside_the_file(
    client: AsyncClient,
) -> None:
    message_id = await finished_turn(client, "Draft a memo on the indemnification provisions")

    response = await client.post(f"/messages/{message_id}/export", json={"format": "markdown"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="' in response.headers["content-disposition"]

    body = response.text
    assert "UNREVIEWED DRAFT" in body
    assert "has not been reviewed by an attorney" in body
    # The notice must precede the content, not sit in a footer nobody scrolls to.
    assert body.index("UNREVIEWED DRAFT") < body.index("# ")


async def test_the_docx_export_is_a_real_word_file_carrying_the_notice(
    client: AsyncClient,
) -> None:
    message_id = await finished_turn(client, "Draft a memo on the indemnification provisions")

    response = await client.post(f"/messages/{message_id}/export", json={"format": "docx"})
    assert response.status_code == 200
    assert "openxmlformats" in response.headers["content-type"]
    # A .docx is a zip; this is the cheapest proof it is not a text file mislabelled.
    assert response.content[:2] == b"PK"

    text = _docx_text(response.content)
    assert "UNREVIEWED DRAFT" in text
    assert "has not been reviewed by an attorney" in text


async def test_the_attorney_placeholder_survives_both_formats(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "Draft a memo on the indemnification provisions")

    markdown = (
        await client.post(f"/messages/{message_id}/export", json={"format": "markdown"})
    ).text
    docx_text = _docx_text(
        (await client.post(f"/messages/{message_id}/export", json={"format": "docx"})).content
    )

    # FR-16: the marker is styled on render and must round-trip verbatim. A renderer
    # that rewrote or dropped it would hide exactly the gaps it exists to flag.
    for rendered in (markdown, docx_text):
        assert "[ATTORNEY INPUT REQUIRED]" in rendered

    # And the notice counts them, so a reader knows how many without hunting.
    assert "passage(s) are marked" in markdown


async def test_citations_are_listed_so_the_file_can_be_checked(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "What are the indemnification obligations?")

    body = (await client.post(f"/messages/{message_id}/export", json={"format": "markdown"})).text

    stored = (await client.get(f"/chat/{message_id}/citations")).json()["citations"]
    assert stored, "the turn under test should be grounded"
    assert "## Sources" in body
    for citation in stored:
        # Verbatim, with its page, so a reader can go and check it.
        assert citation["citedText"].strip() in body
        assert f"page {citation['page']}" in body


async def test_chat_only_markers_do_not_leak_into_the_file(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "What are the indemnification obligations?")

    body = (await client.post(f"/messages/{message_id}/export", json={"format": "markdown"})).text
    # `{{cite:1}}` is a rendering detail of the chat UI; in a document it reads as
    # noise, so it becomes a bracketed reference into the Sources list.
    assert "{{cite:" not in body
    assert "[1]" in body


async def test_a_summary_export_states_every_absence(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "Summarise the key terms")

    body = (await client.post(f"/messages/{message_id}/export", json={"format": "markdown"})).text

    # FR-13, in the exported file: all nine headings present, absences stated. A
    # heading quietly omitted is indistinguishable from one nobody looked at.
    for label in (
        "Parties",
        "Term & renewal",
        "Obligations",
        "Payment terms",
        "Termination triggers",
        "Governing law & venue",
        "Liability & indemnification",
        "Unusual provisions",
        "Open items",
    ):
        assert f"## {label}" in body, f"missing heading: {label}"
    assert "Not addressed in the documents in scope" in body


async def test_the_title_is_not_printed_twice(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "Draft a memo on the indemnification provisions")

    body = (await client.post(f"/messages/{message_id}/export", json={"format": "markdown"})).text
    # The draft body opens with its own heading and the renderer adds the title, so
    # the same line appeared twice before the duplicate was stripped.
    headings = [line.strip() for line in body.splitlines() if line.startswith("#")]
    assert len(headings) == len(set(headings)), f"duplicate heading in {headings}"


# ---------------------------------------------------------------------------
# Export failures
# ---------------------------------------------------------------------------


async def test_exporting_an_unknown_message_is_404(client: AsyncClient) -> None:
    response = await client.post("/messages/a-nope/export", json={"format": "markdown"})
    assert response.status_code == 404
    assert response.json()["code"] == "message_not_found"


async def test_an_unsupported_format_is_refused(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "What are the indemnification obligations?")
    response = await client.post(f"/messages/{message_id}/export", json={"format": "pdf"})
    assert response.status_code == 422


async def test_the_default_format_is_docx(client: AsyncClient) -> None:
    message_id = await finished_turn(client, "What are the indemnification obligations?")
    response = await client.post(f"/messages/{message_id}/export", json={})
    assert response.status_code == 200
    assert "openxmlformats" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# Audit (FR-20, NFR-9)
# ---------------------------------------------------------------------------


async def test_the_audit_log_records_each_generation(client: AsyncClient) -> None:
    await finished_turn(client, "What are the indemnification obligations?")

    page = (await client.get("/audit")).json()
    assert page["total"] == 1

    entry = page["entries"][0]
    # FR-20 names what a row has to carry.
    assert entry["feature"] == "ask"
    assert entry["question"] == "What are the indemnification obligations?"
    assert entry["documentIds"]
    assert entry["groundingSpans"], "the spans that grounded the answer are the record"
    assert entry["modelId"]
    assert entry["retrievalPath"] == "whole_document"
    assert entry["latencyMs"] >= 0
    assert entry["outcome"] == "ok"


async def test_the_audit_log_can_be_filtered(client: AsyncClient) -> None:
    await finished_turn(client, "What are the indemnification obligations?")
    await finished_turn(client, "Summarise the key terms")

    everything = (await client.get("/audit")).json()
    assert everything["total"] == 2

    summaries = (await client.get("/audit?feature=summary")).json()
    assert summaries["total"] == 1
    assert {e["feature"] for e in summaries["entries"]} == {"summary"}


async def test_audit_paging_reports_the_full_total(client: AsyncClient) -> None:
    await finished_turn(client, "What are the indemnification obligations?")
    await finished_turn(client, "Summarise the key terms")

    page = (await client.get("/audit?limit=1")).json()
    assert len(page["entries"]) == 1
    # The total is the whole set, not the page — otherwise a client cannot page.
    assert page["total"] == 2


async def test_the_audit_export_is_jsonl_with_iso_timestamps(client: AsyncClient) -> None:
    await finished_turn(client, "What are the indemnification obligations?")

    response = await client.get("/audit/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "legal-lens-audit.jsonl" in response.headers["content-disposition"]

    lines = [line for line in response.text.splitlines() if line.strip()]
    assert lines
    for line in lines:
        row = json.loads(line)
        # `json.dumps(default=str)` emitted "2026-10-10 13:37:43+00:00" — a space
        # instead of the ISO "T" — which disagreed with GET /audit about the same row
        # and left a non-ISO timestamp in an archive meant to be machine-readable.
        parsed = datetime.fromisoformat(row["createdAt"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert "T" in row["createdAt"]


async def test_the_audit_routes_agree_about_the_same_row(client: AsyncClient) -> None:
    await finished_turn(client, "What are the indemnification obligations?")

    listed = (await client.get("/audit")).json()["entries"][0]
    exported = json.loads((await client.get("/audit/export")).text.splitlines()[0])

    assert listed["id"] == exported["id"]
    assert listed["createdAt"] == exported["createdAt"]


async def test_the_audit_log_is_scoped_to_its_owner(client: AsyncClient, services) -> None:
    await finished_turn(client, "What are the indemnification obligations?")

    entries, total = await services.audit.list("owner-someone-else")
    assert (entries, total) == ([], 0)
