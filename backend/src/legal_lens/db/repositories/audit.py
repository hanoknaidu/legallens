from __future__ import annotations

import uuid
from dataclasses import asdict

import aiosqlite

from ...clock import iso
from ...schemas import AuditEntry, Citation, RewriteAudit
from ..session import Database, from_json, to_json


def _row_keys(row: aiosqlite.Row) -> frozenset[str]:
    """The columns this row actually has.

    `SELECT *` on a database that has not been migrated yet returns fewer columns
    than the current schema declares, and `row["coverage"]` on a missing column
    raises rather than returning None. Reads therefore ask before indexing.
    """
    return frozenset(row.keys())


def _to_entry(row: aiosqlite.Row) -> AuditEntry:
    keys = _row_keys(row)
    raw_rewrite = row["rewrite"] if "rewrite" in keys else None
    rewrite = from_json(raw_rewrite, None) if raw_rewrite else None
    coverage = row["coverage"] if "coverage" in keys else None
    return AuditEntry(
        id=row["id"],
        created_at=row["created_at"],
        owner_id=row["owner_id"],
        feature=row["feature"],
        conversation_id=row["conversation_id"],
        message_id=row["message_id"],
        question=row["question"],
        document_ids=from_json(row["document_ids"], []),
        grounding_spans=[Citation(**c) for c in from_json(row["grounding_spans"], [])],
        retrieval_path=row["retrieval_path"],
        coverage=None if coverage is None else bool(coverage),
        rewrite=RewriteAudit(**rewrite) if rewrite else None,
        model_id=row["model_id"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_write_tokens=row["cache_write_tokens"],
        cost_usd=row["cost_usd"],
        latency_ms=row["latency_ms"],
        abstained=bool(row["abstained"]),
        grounded=bool(row["grounded"]),
        outcome=row["outcome"],
    )


class AuditRepository:
    """One row per generation (FR-20), plus the per-call cost rows behind it.

    Cost is tracked separately from the audit row because one generation can make
    several model calls — intent routing, a query rewrite, a two-pass summary — and
    NFR-10 is a budget per *question*, which only adds up if each call is recorded.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        owner_id: str,
        feature: str,
        conversation_id: str | None,
        message_id: str | None,
        question: str | None,
        document_ids: list[str],
        grounding_spans: list[Citation],
        retrieval_path: str | None,
        model_id: str | None,
        coverage: bool | None = None,
        rewrite: object | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        abstained: bool = False,
        grounded: bool = True,
        outcome: str = "ok",
    ) -> str:
        audit_id = f"aud-{uuid.uuid4().hex[:12]}"
        await self._db.execute(
            """INSERT INTO audit_log
                   (id, created_at, owner_id, feature, conversation_id, message_id,
                    question, document_ids, grounding_spans, retrieval_path, coverage,
                    rewrite, model_id,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    cost_usd, latency_ms, abstained, grounded, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                audit_id,
                iso(),
                owner_id,
                feature,
                conversation_id,
                message_id,
                question,
                to_json(document_ids),
                to_json([c.model_dump(by_alias=False) for c in grounding_spans]),
                retrieval_path,
                None if coverage is None else int(coverage),
                to_json(asdict(rewrite)) if rewrite is not None else None,
                model_id,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                latency_ms,
                int(abstained),
                int(grounded),
                outcome,
            ),
        )
        return audit_id

    async def record_cost(
        self,
        *,
        owner_id: str,
        audit_id: str | None,
        purpose: str,
        model_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        await self._db.execute(
            """INSERT INTO usage_costs
                   (id, created_at, owner_id, audit_id, purpose, model_id, input_tokens,
                    output_tokens, cache_read_tokens, cache_write_tokens, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"cost-{uuid.uuid4().hex[:12]}",
                iso(),
                owner_id,
                audit_id,
                purpose,
                model_id,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
            ),
        )

    async def list(
        self,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
        feature: str | None = None,
        conversation_id: str | None = None,
    ) -> tuple[list[AuditEntry], int]:
        where = ["owner_id = ?"]
        params: list[object] = [owner_id]
        if feature:
            where.append("feature = ?")
            params.append(feature)
        if conversation_id:
            where.append("conversation_id = ?")
            params.append(conversation_id)
        clause = " AND ".join(where)

        total_row = await self._db.fetch_one(
            f"SELECT COUNT(*) AS n FROM audit_log WHERE {clause}", tuple(params)
        )
        rows = await self._db.fetch_all(
            f"""SELECT * FROM audit_log WHERE {clause}
                ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return [_to_entry(r) for r in rows], (int(total_row["n"]) if total_row else 0)

    async def total_cost(self, owner_id: str) -> float:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage_costs WHERE owner_id = ?",
            (owner_id,),
        )
        return round(float(row["total"]), 6) if row else 0.0

    async def cost_by_purpose(self, owner_id: str) -> list[dict[str, object]]:
        """Spend grouped by which step spent it, dearest first.

        NFR-10 is a budget per question, and a single total cannot say whether a
        question's cost is the answer or the plumbing around it. The per-call rows
        have always been written; until this read existed nothing in the service
        could see them, which is how the routing steps came to be 17% of a turn
        without anyone noticing. A number nobody can read is not a measurement.
        """
        rows = await self._db.fetch_all(
            """SELECT purpose,
                      COUNT(*)                        AS calls,
                      COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(cache_read_tokens), 0)  AS cache_read_tokens,
                      COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                      COALESCE(SUM(cost_usd), 0)      AS cost_usd
                 FROM usage_costs
                WHERE owner_id = ?
             GROUP BY purpose
             ORDER BY cost_usd DESC""",
            (owner_id,),
        )
        return [
            {
                "purpose": r["purpose"],
                "calls": int(r["calls"]),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "cache_read_tokens": int(r["cache_read_tokens"]),
                "cache_write_tokens": int(r["cache_write_tokens"]),
                "cost_usd": round(float(r["cost_usd"]), 6),
            }
            for r in rows
        ]
