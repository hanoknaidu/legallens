import { expect, test } from "@playwright/test";
import {
  EXISTING_DOC_TITLE,
  addToScope,
  apiMessages,
  apiPage,
  containsSpan,
  createConversation,
  sendAndWait,
  ui,
} from "../helpers/app";

/** The nine fields fixed by the BRD, in contract order. */
const SUMMARY_LABELS = [
  "Parties",
  "Term & renewal",
  "Obligations",
  "Payment terms",
  "Termination triggers",
  "Governing law & venue",
  "Liability & indemnification",
  "Unusual provisions",
  "Open items",
];

/**
 * TC-04 — scenario 2, first half: a new conversation, an existing document picked from
 * the scope menu, and a summary asked for.
 *
 * The assertion that matters is the one about absence: all nine fields are present and
 * an absent one says so, rather than being left out. A reader cannot otherwise tell a
 * missing clause from a missing answer.
 */
test.describe.serial("TC-04 summarise an existing document", () => {
  let conversationId: string;

  test("TC-04.1 the scope menu offers the indexed documents and takes a selection", async ({
    page,
  }) => {
    await page.goto("/");
    conversationId = await createConversation(page);

    await ui.scopeButton(page).click();
    const items = page.getByRole("menuitem");
    await expect(items.filter({ hasText: EXISTING_DOC_TITLE })).toBeVisible();
    // Nothing that is not indexed should be offerable as a source.
    await expect(items.filter({ hasText: "No indexed documents yet" })).toHaveCount(0);
    await page.keyboard.press("Escape");

    await addToScope(page, EXISTING_DOC_TITLE);
    // The chip is removable, which is the only way to narrow a scope back down.
    await expect(
      page.getByRole("button", { name: `Remove ${EXISTING_DOC_TITLE} from scope` }),
    ).toBeVisible();
  });

  test("TC-04.2 a summary request is routed to the summary workflow", async ({
    page,
    request,
  }) => {
    // Each test gets its own page, so the thread is reopened and the scope re-picked.
    await page.goto(`/chat/${conversationId}`);
    await addToScope(page, EXISTING_DOC_TITLE);
    await sendAndWait(page, `Summarise ${EXISTING_DOC_TITLE}.`);

    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(answer.intent, "the router chose `summary`").toBe("summary");

    // A summary turn has to leave the reader with *something*. When the nine-heading
    // parser cannot recover every field it retries once and then gives up, and the
    // turn is persisted with `summary: null` and an empty `content` — a blank bubble
    // under a "Summary / N sources" header, with no notice saying anything went wrong.
    // Checked before the payload assertion so the failure names that case.
    expect(
      Boolean(answer.summary) || (answer.content ?? "").trim().length > 0,
      "the summary turn produced neither a structured payload nor any prose — " +
        "the user is shown an empty answer",
    ).toBeTruthy();
    expect(answer.summary, "a structured summary payload came back, not prose").toBeTruthy();
  });

  test("TC-04.3 the structured summary renders all nine fields, absences included", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const fields = stored.filter((m) => m.role === "assistant").at(-1)!.summary!;

    expect(fields.map((f) => f.label), "nine fields, in contract order").toEqual(
      SUMMARY_LABELS,
    );
    // An absent field is explicitly null, never omitted.
    for (const f of fields) {
      expect(f, `${f.label} carries a value key`).toHaveProperty("value");
    }

    await expect(page.getByRole("heading", { name: "Structured summary" })).toBeVisible();
    const populated = fields.filter((f) => f.value).length;
    await expect(page.getByText(`${populated} of 9 populated`)).toBeVisible();

    for (const f of fields) {
      await expect(page.getByText(f.label, { exact: true })).toBeVisible();
      if (!f.value) {
        // The card must say so rather than render an empty row.
        continue;
      }
    }
    const absent = fields.filter((f) => !f.value).length;
    await expect(page.getByText("Not found in this document")).toHaveCount(absent);
    expect(
      populated,
      "a five-page statute should populate at least a couple of fields",
    ).toBeGreaterThan(0);
  });

  test("TC-04.4 every populated field cites a source that exists", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    const citations = answer.citations ?? [];
    expect(citations.length, "the summary is cited").toBeGreaterThan(0);

    for (const f of answer.summary!.filter((x) => x.value)) {
      expect(f.cites.length, `${f.label} cites at least one source`).toBeGreaterThan(0);
      for (const n of f.cites) {
        expect(
          citations.some((c) => c.n === n),
          `${f.label} cites source ${n}, which is in the citation list`,
        ).toBeTruthy();
      }
    }
  });

  test("TC-04.5 every chip in the card opens its page with the passage marked", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const citations = stored.filter((m) => m.role === "assistant").at(-1)!.citations ?? [];

    const chips = ui.citationChips(page);
    const count = await chips.count();
    expect(count, "citation chips are rendered in the summary card").toBeGreaterThan(0);

    const unmarked: string[] = [];
    // Distinct sources only: the same source is chipped on several fields.
    const seen = new Set<string>();
    for (let i = 0; i < count; i++) {
      const chip = chips.nth(i);
      const label = (await chip.getAttribute("aria-label")) ?? String(i);
      if (seen.has(label)) continue;
      seen.add(label);

      await chip.click();
      const panel = ui.docPanel(page);
      await expect(panel).toBeVisible();

      const notFound = page.getByText(
        "The cited passage was not found in this page's text.",
      );
      if ((await notFound.count()) > 0) unmarked.push(label);
      else await expect(panel.locator("mark").first()).toBeVisible();

      await panel.press("Escape");
      await expect(panel).toHaveCount(0);
    }

    expect(
      unmarked,
      "every chip highlights its passage in the viewer\n" + unmarked.join("\n"),
    ).toEqual([]);
    expect(citations.length).toBeGreaterThan(0);
  });

  test("TC-04.6 the summary survives a reload", async ({ page }) => {
    await page.goto(`/chat/${conversationId}`);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Structured summary" })).toBeVisible();
    await expect(page.getByText("Summary", { exact: true })).toBeVisible();
    for (const label of SUMMARY_LABELS) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
  });

  /**
   * Last in the file on purpose.
   *
   * This one is intermittent — it depends on whether the model emits a citation that
   * spans two page blocks on this particular run — and a failure inside a `.serial`
   * describe aborts everything after it. Run last, it can fail without taking four
   * working assertions down with it.
   */
  test("TC-04.7 every citation's quoted text is on the page it names", async ({ request }) => {
    const stored = await apiMessages(request, conversationId);
    const citations = stored.filter((m) => m.role === "assistant").at(-1)!.citations ?? [];
    expect(citations.length).toBeGreaterThan(0);

    // The invariant the viewer depends on: the quoted text appears verbatim on the one
    // page the citation names. The viewer highlights by searching that page's text, so
    // a span that is not there opens the right page and marks nothing.
    const offenders: string[] = [];
    for (const c of citations) {
      const pageText = (await apiPage(request, c.documentId, c.page)).text;
      if (!containsSpan(pageText, c.citedText)) {
        offenders.push(
          `source ${c.n}: ${c.citedText.length} chars attributed to ${c.documentTitle} ` +
            `page ${c.page}, whose text is ${pageText.length} chars`,
        );
      }
    }
    expect(
      offenders,
      "every citation's quoted text is on the page it names\n" + offenders.join("\n"),
    ).toEqual([]);
  });
});
