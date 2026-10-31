"""The API contract.

These models are the wire format the React frontend already codes against
(`frontend/src/types/domain.ts`). The frontend is camelCase and the backend is
snake_case, so every model carries a camelCase alias and serialises by alias. Field
names and nullability here are load-bearing: `SummaryField.value is None` is how an
absent clause is reported (FR-13), not an omission, and `abstained` is a correct
outcome rather than an error (FR-9).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

Intent = Literal["ask", "summary", "draft"]
MessageRole = Literal["user", "assistant"]
IngestStatus = Literal["queued", "processing", "ready", "failed"]
DocumentKind = Literal["msa", "nda", "complaint", "other"]
#: Where a document's page text came from. `ocr` means it is a transcription, so a
#: citation drawn from it quotes what the OCR engine produced rather than what the
#: document provably says — which the UI has to be able to show.
TextSource = Literal["native", "ocr"]
ExportFormat = Literal["docx", "markdown"]


class Wire(BaseModel):
    """Base for everything crossing the network boundary."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        ser_json_timedelta="float",
    )


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


class Matter(Wire):
    id: str
    name: str
    client: str


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class LegalDocument(Wire):
    id: str
    matter_id: str
    title: str
    filename: str
    kind: DocumentKind
    pages: int
    size_bytes: int
    status: IngestStatus
    # Populated only when status == "failed" — carries the FR-2 scanned-PDF rejection
    # verbatim, because a user who is told "failed" and not why will re-upload the
    # same file.
    failure_reason: str | None = None
    # `ocr` marks text recovered by transcription rather than read from a text layer.
    # Surfaced on the wire because a citation into transcribed text is weaker evidence
    # than one into the document's own text, and the reader deserves to know which.
    text_source: TextSource = "native"
    uploaded_at: datetime


class DocumentPage(Wire):
    """One page of a document's extracted text layer.

    Page-granular because a citation resolves to a page (FR-7) and the viewer opens to
    one (FR-8). The viewer highlights by finding a citation's `cited_text` inside this
    `text`, so the extraction that produces it has to preserve the cited span verbatim —
    a normalisation that collapsed whitespace here would produce a chip that opens the
    right page and highlights nothing.
    """

    document_id: str
    page: int
    text: str
    char_count: int = 0


class DocumentStatus(Wire):
    id: str
    status: IngestStatus
    failure_reason: str | None = None
    text_source: TextSource = "native"
    pages: int = 0
    # Surfaced so the library can show real progress instead of a spinner that lies.
    chunks_indexed: int = 0


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class Citation(Wire):
    """One grounded span.

    `n` is the number rendered as a chip and referenced by a `{{cite:n}}` marker in the
    message body. `page` is 1-indexed and is what the viewer opens to.
    """

    n: int
    document_id: str
    document_title: str
    page: int
    cited_text: str


class SummaryField(Wire):
    key: str
    label: str
    # None means the clause is absent from the document. FR-13 requires that this be
    # reported rather than omitted, so the field is always present and the value is null.
    value: str | None = None
    cites: list[int] = Field(default_factory=list)


class DraftPayload(Wire):
    title: str
    # Markdown. `[ATTORNEY INPUT REQUIRED]` markers are styled on render (FR-16) and
    # must survive round-tripping untouched.
    body: str


class Message(Wire):
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    created_at: datetime
    intent: Intent | None = None
    citations: list[Citation] | None = None
    # True when every factual assertion carried a citation (NFR-1).
    grounded: bool | None = None
    # True when the documents do not support an answer. A correct outcome (FR-9).
    abstained: bool | None = None
    summary: list[SummaryField] | None = None
    draft: DraftPayload | None = None
    scoped_document_ids: list[str] | None = None


class Conversation(Wire):
    id: str
    title: str
    matter_id: str
    created_at: datetime
    updated_at: datetime
    last_intent: Intent | None = None


#: One limit for a conversation title, shared by create and rename. They disagreed
#: before this existed: `POST` accepted a 300-character title that `PATCH` rejected
#: with a 422, so a conversation could be created in a state it could not be renamed
#: back into.
TITLE_MAX_LENGTH = 200


def _validate_title(value: str) -> str:
    """Trim, and refuse a blank title.

    `min_length=1` alone admits "   ", which would name a conversation whitespace:
    the sidebar row renders empty, and because the stored value is not the default
    string the first-user-turn rename would never replace it.
    """
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("title cannot be blank")
    return trimmed


class CreateConversationRequest(Wire):
    # Required. The client names the conversation — including the placeholder it
    # shows for an unnamed one — rather than relying on a server-side default, so
    # there is exactly one place the initial title is decided.
    #
    # The auto-naming rule still applies: a conversation whose title is the
    # placeholder `DEFAULT_TITLE` is renamed by its first user turn, so the client
    # must send that exact string for an unnamed conversation. `test_default_title`
    # pins the frontend's constant against the backend's.
    title: str = Field(
        min_length=1,
        max_length=TITLE_MAX_LENGTH,
        description=(
            'Name for the conversation. Send the placeholder "New conversation" for an '
            "unnamed one — the first user message then replaces it with its own text, "
            "truncated to 52 characters."
        ),
        examples=["Indemnification caps and carve-outs"],
    )
    matter_id: str | None = Field(
        default=None,
        description="Matter to file the conversation under. Defaults to the seeded matter.",
        examples=["m-001"],
    )

    @field_validator("title")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _validate_title(value)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {"title": "Indemnification caps and carve-outs", "matterId": "m-001"},
                {"title": "New conversation", "matterId": "m-001"},
                {"title": "Venue and governing law"},
            ]
        },
    )


class RenameConversationRequest(Wire):
    title: str = Field(
        min_length=1,
        max_length=TITLE_MAX_LENGTH,
        description="New name for the conversation.",
        examples=["Early termination rights under the MSA"],
    )

    @field_validator("title")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _validate_title(value)


class SendMessageRequest(Wire):
    conversation_id: str
    content: str = Field(min_length=1, max_length=8000)
    scoped_document_ids: list[str] | None = None


class SendMessageResponse(Wire):
    user_message: Message
    # The id to open the SSE stream with. The assistant turn does not exist yet.
    assistant_message_id: str
    intent: Intent


class StopRequest(Wire):
    # Optional: the frontend's stop button aborts the stream and posts an empty body.
    # Phase 1 is single-user, so an empty body cancels whatever that user has in flight.
    message_id: str | None = None


class CitationsResponse(Wire):
    message_id: str
    citations: list[Citation]


class ExportRequest(Wire):
    format: ExportFormat = "docx"


# ---------------------------------------------------------------------------
# Audit, health, metrics
# ---------------------------------------------------------------------------


class RewriteAudit(Wire):
    """The pre-retrieval query rewrite, as the audit row stores it.

    On the audit entry rather than only in the logs: a wrong rewrite fails invisibly —
    retrieval fetches the wrong passages and the answer reads as confident — so "what
    did we actually search for?" has to be answerable from the record of the turn.
    """

    original: str
    searched_for: str
    rewritten: bool
    #: `empty_history`, `model`, `declined`, `unusable` or `error`.
    source: str
    reason: str = ""


class AuditEntry(Wire):
    """One generation, as FR-20 requires it to be logged."""

    id: str
    created_at: datetime
    owner_id: str
    feature: str
    conversation_id: str | None = None
    message_id: str | None = None
    question: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    # The cited spans that grounded the answer — the record of *what the model was
    # shown and used*, which is the part that matters when an answer is challenged.
    grounding_spans: list[Citation] = Field(default_factory=list)
    retrieval_path: str | None = None
    # Whether the model was shown every page in scope. `None` means the turn did not
    # record it, which is deliberately distinct from `False` — only one of those two
    # says an absence claim would have been unsafe.
    coverage: bool | None = None
    rewrite: RewriteAudit | None = None
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    abstained: bool = False
    grounded: bool = True
    outcome: str = "ok"


class CostByPurpose(Wire):
    """What one step of the pipeline has spent (NFR-10)."""

    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


class HealthResponse(Wire):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    checks: dict[str, str]
