import { http, HttpResponse, delay } from "msw";
import { msgStore } from "./conversations";

/**
 * Export, mocked.
 *
 * The bytes are a real Markdown file for either format — a mock cannot produce a
 * valid .docx, and pretending to would hand the user a corrupt download. What this
 * reproduces is the contract the UI depends on: the status codes, the
 * `Content-Disposition` filename, and the FR-17 notice being *inside* the file.
 */
const NOTICE = [
  "> **UNREVIEWED DRAFT — REQUIRES ATTORNEY REVIEW**",
  ">",
  "> This document was generated from the source documents named in its citations and",
  "> has not been reviewed by an attorney. It is not legal advice and must not be",
  "> filed, served, sent to a counterparty or relied upon until a licensed attorney",
  "> has reviewed it.",
  "",
].join("\n");

export const exportHandlers = [
  http.post("/api/messages/:id/export", async ({ params, request }) => {
    await delay(320);
    const { format } = (await request.json()) as { format: "docx" | "markdown" };

    const message = Object.values(msgStore)
      .flat()
      .find((m) => m.id === params.id);

    if (!message) {
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Message not found",
          status: 404,
          detail: `No message with id ${params.id}.`,
          code: "message_not_found",
        },
        { status: 404, headers: { "Content-Type": "application/problem+json" } },
      );
    }

    const body = message.draft?.body ?? message.content;
    if (!body?.trim() && !message.summary) {
      return HttpResponse.json(
        {
          type: "about:blank",
          title: "Nothing to export",
          status: 409,
          detail: "This message has no draft, summary or answer text to export.",
          code: "nothing_to_export",
        },
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      );
    }

    const title = message.draft?.title ?? (message.summary ? "Document summary" : "Answer");
    const sections = message.summary
      ? message.summary
          .map(
            (f) =>
              `## ${f.label}\n\n${f.value ?? "*Not addressed in the documents in scope.*"}\n`,
          )
          .join("\n")
      : body;

    const file = `${NOTICE}\n# ${title}\n\n${sections}\n`;
    const slug =
      title
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 60) || "legal-lens-export";

    return new HttpResponse(file, {
      status: 200,
      headers: {
        "Content-Type":
          format === "docx"
            ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            : "text/markdown; charset=utf-8",
        "Content-Disposition": `attachment; filename="${slug}.${format === "docx" ? "docx" : "md"}"`,
      },
    });
  }),
];
