from __future__ import annotations

import aiosqlite

from ...clock import iso
from ...schemas import DocumentPage, DocumentStatus, IngestStatus, LegalDocument
from ..session import Database


def _to_document(row: aiosqlite.Row) -> LegalDocument:
    return LegalDocument(
        id=row["id"],
        matter_id=row["matter_id"],
        title=row["title"],
        filename=row["filename"],
        kind=row["kind"],
        pages=row["pages"],
        size_bytes=row["size_bytes"],
        status=row["status"],
        failure_reason=row["failure_reason"],
        text_source=row["text_source"],
        uploaded_at=row["uploaded_at"],
    )


_COLUMNS = """id, matter_id, title, filename, kind, pages, size_bytes, status,
              failure_reason, text_source, uploaded_at"""


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list(self, owner_id: str, matter_id: str | None = None) -> list[LegalDocument]:
        if matter_id:
            rows = await self._db.fetch_all(
                f"""SELECT {_COLUMNS} FROM documents
                    WHERE owner_id = ? AND matter_id = ?
                    ORDER BY uploaded_at DESC, rowid DESC""",
                (owner_id, matter_id),
            )
        else:
            rows = await self._db.fetch_all(
                f"""SELECT {_COLUMNS} FROM documents WHERE owner_id = ?
                    ORDER BY uploaded_at DESC, rowid DESC""",
                (owner_id,),
            )
        return [_to_document(r) for r in rows]

    async def get(self, owner_id: str, document_id: str) -> LegalDocument | None:
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM documents WHERE id = ? AND owner_id = ?",
            (document_id, owner_id),
        )
        return _to_document(row) if row else None

    async def status(self, owner_id: str, document_id: str) -> DocumentStatus | None:
        row = await self._db.fetch_one(
            """SELECT d.id, d.status, d.failure_reason, d.pages, d.text_source,
                      (SELECT COUNT(*) FROM chunk_metadata c
                        WHERE c.document_id = d.id AND c.indexed = 1) AS chunks_indexed
               FROM documents d WHERE d.id = ? AND d.owner_id = ?""",
            (document_id, owner_id),
        )
        if row is None:
            return None
        return DocumentStatus(
            id=row["id"],
            status=row["status"],
            failure_reason=row["failure_reason"],
            pages=row["pages"],
            text_source=row["text_source"],
            chunks_indexed=row["chunks_indexed"],
        )

    async def object_key(self, owner_id: str, document_id: str) -> tuple[str, str, str] | None:
        """`(object_key, filename, media_type)` for serving the original file."""
        row = await self._db.fetch_one(
            """SELECT object_key, filename, media_type FROM documents
               WHERE id = ? AND owner_id = ?""",
            (document_id, owner_id),
        )
        if row is None or not row["object_key"]:
            return None
        return row["object_key"], row["filename"], row["media_type"]

    async def create(
        self,
        owner_id: str,
        document_id: str,
        matter_id: str,
        title: str,
        filename: str,
        kind: str,
        media_type: str,
        size_bytes: int,
        object_key: str,
        checksum: str,
    ) -> LegalDocument:
        now = iso()
        await self._db.execute(
            """INSERT INTO documents
                   (id, owner_id, matter_id, title, filename, kind, media_type, pages,
                    size_bytes, status, object_key, checksum, token_estimate, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 'queued', ?, ?, 0, ?)""",
            (
                document_id,
                owner_id,
                matter_id,
                title,
                filename,
                kind,
                media_type,
                size_bytes,
                object_key,
                checksum,
                now,
            ),
        )
        return LegalDocument(
            id=document_id,
            matter_id=matter_id,
            title=title,
            filename=filename,
            kind=kind,
            pages=0,
            size_bytes=size_bytes,
            status="queued",
            text_source="native",
            uploaded_at=now,
        )

    async def mark_status(
        self,
        document_id: str,
        status: IngestStatus,
        failure_reason: str | None = None,
        pages: int | None = None,
        token_estimate: int | None = None,
        text_source: str | None = None,
    ) -> None:
        # The ingestion worker owns the document row by id; it has no owner to scope by
        # beyond the one that created it, which the caller already checked.
        await self._db.execute(
            """UPDATE documents
                  SET status = ?,
                      failure_reason = ?,
                      pages = COALESCE(?, pages),
                      token_estimate = COALESCE(?, token_estimate),
                      text_source = COALESCE(?, text_source)
                WHERE id = ?""",
            (status, failure_reason, pages, token_estimate, text_source, document_id),
        )

    async def replace_pages(self, document_id: str, pages: list[tuple[int, str, int]]) -> None:
        """Pages are written as a set, so a re-parse cannot leave stale ones behind."""
        await self._db.execute("DELETE FROM document_pages WHERE document_id = ?", (document_id,))
        await self._db.execute_many(
            """INSERT INTO document_pages (document_id, page, text, char_count)
               VALUES (?, ?, ?, ?)""",
            [(document_id, page, text, chars) for page, text, chars in pages],
        )

    async def page(self, owner_id: str, document_id: str, page: int) -> DocumentPage | None:
        row = await self._db.fetch_one(
            """SELECT p.document_id, p.page, p.text, p.char_count
                 FROM document_pages p
                 JOIN documents d ON d.id = p.document_id
                WHERE p.document_id = ? AND p.page = ? AND d.owner_id = ?""",
            (document_id, page, owner_id),
        )
        if row is None:
            return None
        return DocumentPage(
            document_id=row["document_id"],
            page=row["page"],
            text=row["text"],
            char_count=row["char_count"],
        )

    async def all_pages(self, owner_id: str, document_id: str) -> list[tuple[int, str]]:
        """Every page of one document, in order, as `(page, text)`.

        Used by whole-corpus retrieval, which is the default path below
        `whole_document_token_budget` — so this is the hot read on the chat path.
        """
        rows = await self._db.fetch_all(
            """SELECT p.page, p.text
                 FROM document_pages p
                 JOIN documents d ON d.id = p.document_id
                WHERE p.document_id = ? AND d.owner_id = ? AND d.status = 'ready'
                ORDER BY p.page""",
            (document_id, owner_id),
        )
        return [(r["page"], r["text"]) for r in rows]

    async def indexable_chunks(self) -> list[aiosqlite.Row]:
        """Every chunk of every ready document, for rebuilding the in-memory index.

        The in-memory vector store holds nothing across a restart, so `indexed = 1` in
        SQLite would otherwise be a claim about an index that does not exist.
        """
        return await self._db.fetch_all(
            """SELECT c.id, c.document_id, c.matter_id, c.owner_id, c.page_start,
                      c.page_end, c.text
                 FROM chunk_metadata c
                 JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'ready'
                ORDER BY c.document_id, c.ordinal"""
        )

    async def mark_chunks_indexed(self, document_id: str) -> None:
        await self._db.execute(
            "UPDATE chunk_metadata SET indexed = 1 WHERE document_id = ?", (document_id,)
        )

    async def replace_chunks(self, document_id: str, rows: list[tuple]) -> None:
        await self._db.execute("DELETE FROM chunk_metadata WHERE document_id = ?", (document_id,))
        await self._db.execute_many(
            """INSERT INTO chunk_metadata
                   (id, document_id, matter_id, owner_id, ordinal, page_start, page_end,
                    section, defined_terms, parent_id, text, char_count, indexed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            rows,
        )

    async def delete(self, owner_id: str, document_id: str) -> bool:
        """Pages, chunks and citations cascade inside SQLite (schema.sql).

        What SQLite cannot reach — the object store, the vector store — is enumerated
        in `deletion_jobs` by the caller and reconciled, because NFR-8 requires the
        purge to be provable rather than assumed.
        """
        changed = await self._db.execute(
            "DELETE FROM documents WHERE id = ? AND owner_id = ?", (document_id, owner_id)
        )
        return bool(changed)

    async def record_deletion_job(
        self,
        owner_id: str,
        document_id: str,
        target: str,
        job_id: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO deletion_jobs
                   (id, document_id, owner_id, target, status, detail, requested_at,
                    completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                document_id,
                owner_id,
                target,
                status,
                detail,
                iso(),
                iso() if status != "pending" else None,
            ),
        )

    async def pending_deletions(self) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM deletion_jobs WHERE status = 'pending'"
        )
        return int(row["n"]) if row else 0

    async def count_for_matter(self, owner_id: str, matter_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM documents WHERE owner_id = ? AND matter_id = ?",
            (owner_id, matter_id),
        )
        return int(row["n"]) if row else 0
