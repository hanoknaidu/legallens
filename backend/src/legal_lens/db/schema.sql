-- Legal Lens — SQLite schema.
--
-- Two rules shape this file.
--
-- 1. Owner from day one (BRD §6.1). Phase 1 has no login, but `owner_id` is on every
--    matter, document, conversation and audit row, and every query filters on it. That
--    makes Phase 2 authentication a middleware change instead of a schema migration.
-- 2. Deletion has to be verifiable (NFR-8). Derived artifacts — extracted text, chunks,
--    citations, chunk metadata — hang off the document with ON DELETE CASCADE, so one
--    DELETE provably removes the lot inside SQLite. What SQLite cannot reach (the object
--    store, the vector store) is enumerated in `deletion_jobs` and reconciled, because
--    "we also deleted it from Qdrant" is a claim that needs a record.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS matters (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    client      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matters_owner ON matters(owner_id);

CREATE TABLE IF NOT EXISTS documents (
    id             TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    matter_id      TEXT NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    filename       TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'other',
    media_type     TEXT NOT NULL,
    pages          INTEGER NOT NULL DEFAULT 0,
    size_bytes     INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('queued','processing','ready','failed')),
    failure_reason TEXT,
    object_key     TEXT,
    checksum       TEXT,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    -- Where the page text came from. 'native' is a real text layer; 'ocr' is a
    -- transcription, which means a citation drawn from it quotes what the OCR engine
    -- produced rather than what the document provably says. The UI has to be able to
    -- tell these apart, so the provenance is stored rather than inferred.
    text_source    TEXT NOT NULL DEFAULT 'native'
                   CHECK (text_source IN ('native','ocr')),
    uploaded_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_matter ON documents(owner_id, matter_id);

-- Extracted text, one row per page. Page-granular because a citation resolves to a page
-- (FR-7) and the viewer opens to one (FR-8); storing the document as a single blob would
-- mean recomputing page boundaries on every citation.
CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page        INTEGER NOT NULL,
    text        TEXT NOT NULL,
    char_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (document_id, page)
);

CREATE TABLE IF NOT EXISTS chunk_metadata (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    matter_id     TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,
    page_start    INTEGER NOT NULL,
    page_end      INTEGER NOT NULL,
    section       TEXT,
    -- A chunk that lost the definition of a term it uses produces confidently wrong
    -- retrieval, so the chunker carries defined-term context forward and records it.
    defined_terms TEXT,
    parent_id     TEXT,
    text          TEXT NOT NULL,
    char_count    INTEGER NOT NULL DEFAULT 0,
    indexed       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunk_metadata(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_matter ON chunk_metadata(owner_id, matter_id);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,
    detail       TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_document ON ingestion_jobs(document_id);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    matter_id   TEXT NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    last_intent TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    owner_id            TEXT NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content             TEXT NOT NULL,
    intent              TEXT,
    grounded            INTEGER,
    abstained           INTEGER,
    -- The 9-field summary and the draft are stored as JSON rather than normalised into
    -- columns: they are a rendering payload the frontend round-trips whole, and the
    -- field set is fixed by the BRD, not by the data.
    summary_json        TEXT,
    draft_json          TEXT,
    scoped_document_ids TEXT,
    created_at          TEXT NOT NULL,
    sequence            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, sequence);

CREATE TABLE IF NOT EXISTS message_citations (
    message_id     TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    n              INTEGER NOT NULL,
    document_id    TEXT NOT NULL,
    document_title TEXT NOT NULL,
    page           INTEGER NOT NULL,
    cited_text     TEXT NOT NULL,
    PRIMARY KEY (message_id, n)
);

-- FR-20. One row per generation. Retained 12 months and exportable (NFR-9).
CREATE TABLE IF NOT EXISTS audit_log (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    owner_id          TEXT NOT NULL,
    feature           TEXT NOT NULL,
    conversation_id   TEXT,
    message_id        TEXT,
    question          TEXT,
    document_ids      TEXT NOT NULL DEFAULT '[]',
    grounding_spans   TEXT NOT NULL DEFAULT '[]',
    retrieval_path    TEXT,
    -- Whether the pages sent were *every* page in scope. The flag that licensed the
    -- answer to report an absence, or forbade it; FR-20 is a record of what the model
    -- was shown, and "was it shown everything?" is part of that.
    coverage          INTEGER,
    -- The pre-retrieval query rewrite, as JSON: the original, what was searched for,
    -- the verdict and the model's one-line reason. When an answer is wrong the first
    -- question is whether retrieval was even looking for the right thing, and that is
    -- only answerable if the rewrite was recorded next to the answer it produced.
    rewrite           TEXT,
    model_id          TEXT,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    output_tokens     INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    abstained         INTEGER NOT NULL DEFAULT 0,
    grounded          INTEGER NOT NULL DEFAULT 1,
    outcome           TEXT NOT NULL DEFAULT 'ok'
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(owner_id, created_at DESC);

-- Per-call spend, kept separate from the audit log because one generation can make
-- several model calls (intent route, query rewrite, two-pass summary) and NFR-10 is a
-- budget per *question*, which only adds up if each call is recorded.
CREATE TABLE IF NOT EXISTS usage_costs (
    id                 TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    owner_id           TEXT NOT NULL,
    audit_id           TEXT,
    purpose            TEXT NOT NULL,
    model_id           TEXT NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_costs_created ON usage_costs(owner_id, created_at DESC);

-- NFR-8 requires deletion to be verifiable within 24h, including provider-side copies.
-- A row lands here when a document is deleted and is cleared only once each external
-- store confirms; an unresolved row is the evidence that the purge is incomplete.
CREATE TABLE IF NOT EXISTS deletion_jobs (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    target       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('pending','done','failed')),
    detail       TEXT,
    requested_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_deletion_pending ON deletion_jobs(status, requested_at);
