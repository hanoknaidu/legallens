"""The document routes end to end: upload, poll, read pages, delete.

Ingestion runs as a background task, so these tests wait for a terminal status the way
the frontend does — by polling `/status` — rather than reaching into the pipeline.
That also means they cover the thing most likely to break: a document that never
leaves `processing` because nothing set its state.
"""

from __future__ import annotations

import asyncio

from httpx import AsyncClient

from fixtures import docx_file, scanned_pdf, text_pdf, txt_file

MSA_PAGES = [
    "Section 7.2 Indemnification. The Supplier shall indemnify the Customer against "
    "all third-party claims arising from the Services.",
    "Section 9.1 Governing Law. This Agreement is governed by the laws of Delaware, "
    "and venue lies exclusively in the state courts of New Castle County.",
    "Section 12.1 Insurance. The Supplier shall maintain commercial general liability "
    "insurance with limits of not less than USD 5,000,000 per occurrence.",
]


async def _upload(client: AsyncClient, name: str, data: bytes, **form: str) -> dict:
    response = await client.post(
        "/documents", files={"file": (name, data, "application/octet-stream")}, data=form
    )
    assert response.status_code == 201, response.text
    return response.json()


TERMINAL_TIMEOUT_S = 10.0


async def _await_terminal(client: AsyncClient, document_id: str) -> dict:
    """Poll until the document is `ready` or `failed`, as the frontend does.

    A timeout here is a real failure, not a slow test: it means the pipeline left the
    row mid-flight, which is the state that makes the UI poll forever.
    """
    try:
        async with asyncio.timeout(TERMINAL_TIMEOUT_S):
            while True:
                body = (await client.get(f"/documents/{document_id}/status")).json()
                if body["status"] in ("ready", "failed"):
                    return body
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise AssertionError(
            f"{document_id} never reached a terminal status within {TERMINAL_TIMEOUT_S}s"
        ) from None


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_a_text_pdf_is_ingested_and_its_pages_are_readable(client: AsyncClient) -> None:
    created = await _upload(client, "northwind-arbor-msa.pdf", text_pdf(MSA_PAGES))

    # The POST returns immediately with a queued row rather than waiting for parsing.
    assert created["status"] == "queued"
    assert created["matterId"] == "m-001"
    assert created["kind"] == "msa"
    assert created["title"] == "northwind arbor msa"

    final = await _await_terminal(client, created["id"])
    assert final["status"] == "ready"
    assert final["pages"] == 3

    page = (await client.get(f"/documents/{created['id']}/page/2")).json()
    assert page["page"] == 2
    assert "Delaware" in page["text"]
    assert page["charCount"] == len(page["text"])
    assert page["documentId"] == created["id"]


async def test_cited_text_is_findable_on_the_page_it_names(client: AsyncClient) -> None:
    """The product's core claim, at the storage layer.

    Highlighting works by locating the cited span inside the stored page text. If
    ingestion normalised anything, this is where it shows up — and the symptom in the
    UI would be a chip that opens the right page and highlights nothing.
    """
    created = await _upload(client, "msa.pdf", text_pdf(MSA_PAGES))
    await _await_terminal(client, created["id"])

    span = "commercial general liability insurance"
    page = (await client.get(f"/documents/{created['id']}/page/3")).json()
    assert span in page["text"]


async def test_docx_and_txt_are_ingested(client: AsyncClient) -> None:
    for name, data in (
        ("memo.docx", docx_file(["Payment is due within thirty (30) days."])),
        ("notes.txt", txt_file("Termination requires ninety days written notice.")),
    ):
        created = await _upload(client, name, data)
        final = await _await_terminal(client, created["id"])
        assert final["status"] == "ready", f"{name} -> {final}"
        assert final["pages"] >= 1


async def test_documents_are_listed_newest_first_and_filtered_by_matter(
    client: AsyncClient,
) -> None:
    first = await _upload(client, "one.txt", txt_file("first document"))
    second = await _upload(client, "two.txt", txt_file("second document"))

    listed = (await client.get("/documents")).json()
    assert [d["id"] for d in listed][:2] == [second["id"], first["id"]]

    scoped = (await client.get("/documents?matterId=m-001")).json()
    assert {d["id"] for d in scoped} >= {first["id"], second["id"]}

    assert (await client.get("/documents?matterId=m-nothing")).json() == []


# ---------------------------------------------------------------------------
# FR-2 and the other rejections
# ---------------------------------------------------------------------------


async def test_a_scanned_pdf_is_rejected_with_its_reason(client: AsyncClient) -> None:
    created = await _upload(client, "contract-scan.pdf", scanned_pdf(4))
    final = await _await_terminal(client, created["id"])

    # Rejected, not failed-and-forgotten: the row persists, carries the verbatim
    # reason the frontend renders, and reports the pages it did reach.
    assert final["status"] == "failed"
    assert final["failureReason"].startswith("This PDF has no extractable text layer")
    assert "Optical character recognition" in final["failureReason"]
    assert final["pages"] == 4

    # The document is still fetchable — the library has to be able to show it.
    document = (await client.get(f"/documents/{created['id']}")).json()
    assert document["status"] == "failed"
    assert document["failureReason"] == final["failureReason"]


async def test_a_rejected_document_serves_no_pages(client: AsyncClient) -> None:
    created = await _upload(client, "fax-copy.pdf", scanned_pdf(2))
    await _await_terminal(client, created["id"])

    response = await client.get(f"/documents/{created['id']}/page/1")
    # 409 rather than 404: the document exists, it simply has no text layer, and the
    # viewer must not render an empty page as though the document were blank.
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "document_not_ready"
    assert "no extractable text layer" in body["detail"]


async def test_an_unreadable_file_is_distinguished_from_a_scan(client: AsyncClient) -> None:
    created = await _upload(client, "broken.pdf", b"%PDF-1.7\nnot actually a pdf")
    final = await _await_terminal(client, created["id"])

    assert final["status"] == "failed"
    assert "could not be read" in final["failureReason"]


async def test_an_unsupported_type_is_refused_before_storage(client: AsyncClient) -> None:
    response = await client.post(
        "/documents", files={"file": ("scan.png", b"\x89PNG\r\n", "image/png")}
    )
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


async def test_an_oversized_file_is_refused(client: AsyncClient, settings) -> None:
    oversized = b"x" * (settings.max_upload_bytes + 1)
    response = await client.post("/documents", files={"file": ("big.txt", oversized, "text/plain")})

    assert response.status_code == 413
    body = response.json()
    assert body["code"] == "file_too_large"
    # The detail names both the size and the limit, because "too large" alone does not
    # tell the user what to do.
    assert "50MB limit" in body["detail"]


async def test_an_empty_file_is_refused(client: AsyncClient) -> None:
    response = await client.post("/documents", files={"file": ("empty.txt", b"", "text/plain")})
    assert response.status_code == 422
    assert response.json()["code"] == "empty_file"


async def test_uploading_to_an_unknown_matter_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/documents",
        files={"file": ("a.txt", b"content", "text/plain")},
        data={"matterId": "m-nope"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "matter_not_found"


# ---------------------------------------------------------------------------
# Pages, the original file, and deletion
# ---------------------------------------------------------------------------


async def test_a_page_past_the_end_is_out_of_range(client: AsyncClient) -> None:
    # Long enough to clear `min_chars_per_text_page`; a shorter page would be read as
    # having no text layer, which is the threshold's job but not this test's subject.
    created = await _upload(
        client,
        "short.pdf",
        text_pdf(["Section 1.1 Term. This Agreement commences on the Effective Date."]),
    )
    await _await_terminal(client, created["id"])

    response = await client.get(f"/documents/{created['id']}/page/99")
    assert response.status_code == 416
    assert response.json()["code"] == "page_out_of_range"


async def test_the_original_file_is_served_inline_for_the_viewer(client: AsyncClient) -> None:
    data = text_pdf(["Page one."])
    created = await _upload(client, "original.pdf", data)
    await _await_terminal(client, created["id"])

    response = await client.get(f"/documents/{created['id']}/file")
    assert response.status_code == 200
    # Byte-identical: the viewer renders this with pdf.js, not a re-encoding.
    assert response.content == data
    assert "inline" in response.headers["content-disposition"]
    assert response.headers["accept-ranges"] == "bytes"


async def test_missing_documents_are_404_everywhere(client: AsyncClient) -> None:
    for path in (
        "/documents/d-nope",
        "/documents/d-nope/status",
        "/documents/d-nope/page/1",
        "/documents/d-nope/file",
    ):
        assert (await client.get(path)).status_code == 404, path
    assert (await client.delete("/documents/d-nope")).status_code == 404


async def test_deleting_a_document_removes_its_derived_artifacts(client: AsyncClient) -> None:
    created = await _upload(client, "doomed.pdf", text_pdf(MSA_PAGES))
    await _await_terminal(client, created["id"])

    # It really was there first, so the assertions below are about deletion rather
    # than about something that never existed.
    assert (await client.get(f"/documents/{created['id']}/page/1")).status_code == 200

    assert (await client.delete(f"/documents/{created['id']}")).status_code == 204

    assert (await client.get(f"/documents/{created['id']}")).status_code == 404
    assert (await client.get(f"/documents/{created['id']}/page/1")).status_code == 404
    assert (await client.get(f"/documents/{created['id']}/file")).status_code == 404
    assert created["id"] not in [d["id"] for d in (await client.get("/documents")).json()]


async def test_deletion_leaves_health_reconciled(client: AsyncClient) -> None:
    created = await _upload(client, "gone.txt", txt_file("some text to index"))
    await _await_terminal(client, created["id"])
    await client.delete(f"/documents/{created['id']}")

    # NFR-8: the object store delete is recorded and resolved, so nothing is left
    # pending. A pending row would degrade health, which is the point of tracking it.
    health = (await client.get("/health")).json()
    assert health["checks"]["pending_deletions"] == "ok"


async def test_a_page_too_sparse_to_be_a_text_layer_is_treated_as_a_scan(
    client: AsyncClient,
) -> None:
    """`min_chars_per_text_page` is a deliberate threshold, not an accident.

    A scanned page commonly yields a handful of stray characters rather than none at
    all, so "has a text layer" cannot mean "yielded more than zero bytes". The cost is
    that a genuinely near-empty PDF is called a scan — which is the safer error, since
    indexing it would produce confident answers drawn from almost nothing.
    """
    created = await _upload(client, "sparse.pdf", text_pdf(["Exhibit A"]))
    final = await _await_terminal(client, created["id"])

    assert final["status"] == "failed"
    assert final["failureReason"].startswith("This PDF has no extractable text layer")
