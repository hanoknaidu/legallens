"""Turning a persisted message into a .docx or .md file."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Literal

from ..schemas import Citation, ExportFormat, Message
from . import notice

PLACEHOLDER = re.compile(r"\[ATTORNEY INPUT REQUIRED[^\]]*\]")

#: `{{cite:n}}` markers are a rendering detail of the chat UI. In a file they would
#: read as noise, so they become a bracketed number pointing at the Sources section.
CITE_MARKER = re.compile(r"\{\{cite:(\d+)\}\}")


@dataclass(frozen=True)
class ExportResult:
    content: bytes
    media_type: str
    filename: str


class NothingToExport(Exception):
    """The message carries no draft, summary or answer text."""


def _slug(text: str, fallback: str = "legal-lens-export") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:60].rstrip("-") or fallback


def _inline(text: str) -> str:
    """Rewrite chat-only markup for a document."""
    return CITE_MARKER.sub(lambda m: f"[{m.group(1)}]", text)


def _gap_count(body: str) -> int:
    return len(PLACEHOLDER.findall(body))


LEADING_HEADING = re.compile(r"\A\s*#{1,6}\s*(?P<text>[^\n]+)\n+", re.S)


def _strip_duplicate_heading(body: str, title: str) -> str:
    """Drop the body's opening heading when it repeats the document title.

    A generated draft usually opens with its own heading, and the renderer adds the
    title as well — so the exported file showed the same line twice. Only an exact
    (case- and punctuation-insensitive) match is removed, so a body whose first
    heading is a real section keeps it.
    """
    match = LEADING_HEADING.match(body)
    if match is None:
        return body
    if _slug(match.group("text")) != _slug(title):
        return body
    return body[match.end() :]


def _title_for(message: Message) -> str:
    if message.draft is not None:
        return message.draft.title
    if message.summary is not None:
        return "Document summary"
    return "Answer"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _markdown(message: Message, citations: list[Citation]) -> bytes:
    title = _title_for(message)
    lines: list[str] = []

    # The notice goes first, as a blockquote so it survives being pasted into any
    # Markdown renderer with its emphasis intact.
    lines += [f"> **{notice.TITLE}**", ">", f"> {notice.BODY}", ">"]

    body_source = message.draft.body if message.draft else message.content
    lines += [f"> {notice.gap_sentence(_gap_count(body_source or ''))}", "", f"# {title}", ""]

    if message.draft is not None:
        body = _strip_duplicate_heading(message.draft.body, title)
        lines += [_inline(body).rstrip(), ""]
    elif message.summary is not None:
        for field in message.summary:
            lines += [f"## {field.label}", ""]
            if field.value is None:
                # FR-13: an absence is stated, not omitted. A heading quietly left out
                # is indistinguishable from a heading nobody looked at.
                lines += ["*Not addressed in the documents in scope.*", ""]
            else:
                refs = "".join(f" [{n}]" for n in field.cites)
                lines += [f"{_inline(field.value).rstrip()}{refs}", ""]
    else:
        lines += [_inline(message.content or "").rstrip(), ""]

    if citations:
        lines += [f"## {notice.SOURCES_TITLE}", "", notice.SOURCES_PREAMBLE, ""]
        for citation in citations:
            lines.append(
                f"{citation.n}. **{citation.document_title}**, page {citation.page} — "
                f'"{citation.cited_text.strip()}"'
            )
        lines.append("")

    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------


def _docx(message: Message, citations: list[Citation]) -> bytes:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    document = docx.Document()
    title = _title_for(message)

    # The notice is a shaded, bold block at the very top of page one, so it is the
    # first thing a reader sees and cannot be scrolled past unseen.
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = heading.add_run(notice.TITLE)
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x99, 0x1B, 0x1B)

    body_source = message.draft.body if message.draft else message.content
    for text in (notice.BODY, notice.gap_sentence(_gap_count(body_source or ""))):
        paragraph = document.add_paragraph()
        warn = paragraph.add_run(text)
        warn.font.size = Pt(9)
        warn.font.color.rgb = RGBColor(0x92, 0x40, 0x0E)

    document.add_paragraph()
    document.add_heading(title, level=1)

    if message.draft is not None:
        body = _strip_duplicate_heading(message.draft.body, title)
        _docx_body(document, _inline(body), Pt)
    elif message.summary is not None:
        for field in message.summary:
            document.add_heading(field.label, level=2)
            if field.value is None:
                paragraph = document.add_paragraph()
                absent = paragraph.add_run("Not addressed in the documents in scope.")
                absent.italic = True
            else:
                refs = "".join(f" [{n}]" for n in field.cites)
                document.add_paragraph(f"{_inline(field.value).strip()}{refs}")
    else:
        _docx_body(document, _inline(message.content or ""), Pt)

    if citations:
        document.add_heading(notice.SOURCES_TITLE, level=2)
        preamble = document.add_paragraph()
        intro = preamble.add_run(notice.SOURCES_PREAMBLE)
        intro.font.size = Pt(9)
        intro.italic = True
        for citation in citations:
            paragraph = document.add_paragraph(style="List Number")
            label = paragraph.add_run(f"{citation.document_title}, page {citation.page} — ")
            label.bold = True
            paragraph.add_run(f'"{citation.cited_text.strip()}"')

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _docx_body(document, body: str, Pt) -> None:  # noqa: N803 — the docx unit helper
    """Write Markdown-ish text as Word paragraphs, marking the FR-16 placeholders.

    Not a full Markdown renderer: headings and paragraphs are what these documents
    contain, and the one thing that must not be lost is the placeholder, which is
    highlighted rather than left as plain text so a reader scanning the page sees it.
    """
    from docx.shared import RGBColor

    for block in body.split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 4)
            document.add_heading(stripped.lstrip("#").strip(), level=max(level, 2))
            continue

        paragraph = document.add_paragraph()
        position = 0
        for match in PLACEHOLDER.finditer(stripped):
            if match.start() > position:
                paragraph.add_run(stripped[position : match.start()])
            marker = paragraph.add_run(match.group(0))
            marker.bold = True
            marker.font.color.rgb = RGBColor(0x92, 0x40, 0x0E)
            position = match.end()
        if position < len(stripped):
            paragraph.add_run(stripped[position:])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

MEDIA_TYPES: dict[str, str] = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "markdown": "text/markdown; charset=utf-8",
}
EXTENSIONS: dict[str, str] = {"docx": "docx", "markdown": "md"}


def export_message(
    message: Message, citations: list[Citation], fmt: ExportFormat | Literal["docx", "markdown"]
) -> ExportResult:
    if not (message.draft or message.summary or (message.content or "").strip()):
        raise NothingToExport("the message has no draft, summary or answer text")

    content = _docx(message, citations) if fmt == "docx" else _markdown(message, citations)
    return ExportResult(
        content=content,
        media_type=MEDIA_TYPES[fmt],
        filename=f"{_slug(_title_for(message))}.{EXTENSIONS[fmt]}",
    )
