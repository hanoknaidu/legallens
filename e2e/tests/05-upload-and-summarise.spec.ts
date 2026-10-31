import { expect, test } from "@playwright/test";
import {
  API,
  INGEST_TIMEOUT,
  MSA_FILENAME,
  MSA_FIXTURE,
  MSA_TITLE,
  addToScope,
  apiDocuments,
  apiMessages,
  apiPage,
  containsSpan,
  createConversation,
  documentCard,
  removeFixtureUploads,
  sendAndWait,
  ui,
} from "../helpers/app";

/**
 * TC-05 — scenario 2, second half: upload a document that was never in the database,
 * wait for it to index, then summarise it in a fresh conversation.
 *
 * The fixture is a three-page services agreement with a real text layer, written so
 * that eight of the nine BRD summary fields are genuinely present and one is not —
 * which is what makes "an absent field says so" a test rather than a tautology.
 */
test.describe.serial("TC-05 upload a document and summarise it", () => {
  let documentId: string;
  let conversationId: string;

  test.beforeAll(async ({ request }) => {
    // A second upload of the same file is a second document with the same title, so
    // any copy left by an earlier run is cleared before this one adds its own.
    await removeFixtureUploads(request);
  });

  test("TC-05.1 uploading through the library indexes the document", async ({
    page,
    request,
  }) => {
    await page.goto("/documents");
    const before = await apiDocuments(request);

    await page.locator("input[type=file]").setInputFiles(MSA_FIXTURE);

    const card = documentCard(page, MSA_FILENAME);
    await expect(card).toBeVisible({ timeout: 30_000 });
    // It is queued or indexing first, and reaches ready on its own — no reload.
    await expect(card.getByText("Ready")).toBeVisible({ timeout: INGEST_TIMEOUT });
    await expect(card).toContainText("3 pages");
    // The title is derived from the filename, with the extension and hyphens removed.
    await expect(card.getByRole("link", { name: MSA_TITLE })).toBeVisible();

    // Asserted on identity rather than on a count: the library is shared state, and a
    // count is a claim about every other document as well as this one.
    const after = await apiDocuments(request);
    const knownIds = new Set(before.map((d) => d.id));
    const added = after.filter((d) => !knownIds.has(d.id));
    expect(
      added.map((d) => d.filename),
      `exactly one document was added; library was ${before.map((d) => d.filename).join(", ")}`,
    ).toEqual([MSA_FILENAME]);
    const stored = added[0];
    expect(stored.title).toBe(MSA_TITLE);
    expect(stored.status).toBe("ready");
    expect(stored.pages).toBe(3);
    documentId = stored.id;

    // The heading count tracks the library.
    await expect(page.getByRole("heading", { name: `${after.length} documents` })).toBeVisible();
    expect(after.length, "the upload added one row").toBe(before.length + 1);
  });

  test("TC-05.2 its pages are readable and citable", async ({ page, request }) => {
    const first = await apiPage(request, documentId, 1);
    expect(first.text).toContain("MASTER SERVICES AGREEMENT");
    const second = await apiPage(request, documentId, 2);
    expect(second.text).toContain("FEES AND PAYMENT");

    // And through the viewer, which is what a citation opens.
    await page.goto("/documents");
    await documentCard(page, MSA_TITLE)
      .getByRole("button", { name: `Open ${MSA_TITLE} in the side panel` })
      .click();
    const panel = ui.docPanel(page);
    await expect(panel).toBeVisible();
    await expect(panel.getByText("MASTER SERVICES AGREEMENT")).toBeVisible();
    await expect(panel.getByText("Page 1 of 3", { exact: true })).toBeVisible();
    await panel.press("Escape");
  });

  test("TC-05.3 a new conversation scoped to it returns the nine-field summary", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    conversationId = await createConversation(page);
    await addToScope(page, MSA_TITLE);

    await sendAndWait(page, "Summarise this agreement.");

    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(answer.intent).toBe("summary");
    expect(
      Boolean(answer.summary) || (answer.content ?? "").trim().length > 0,
      "the summary turn produced neither a structured payload nor any prose",
    ).toBeTruthy();
    expect(answer.summary).toBeTruthy();
    expect(answer.summary!).toHaveLength(9);

    await expect(page.getByRole("heading", { name: "Structured summary" })).toBeVisible();

    // The scope was honoured: nothing from another document was cited.
    for (const c of answer.citations ?? []) {
      expect(c.documentId, `source ${c.n} came from the scoped document`).toBe(documentId);
    }
  });

  test("TC-05.4 the summary's facts match the document, field by field", async ({ request }) => {
    const stored = await apiMessages(request, conversationId);
    const fields = stored.filter((m) => m.role === "assistant").at(-1)!.summary!;
    const get = (key: string) => fields.find((f) => f.key === key)!;

    // Each of these is a fact that is unambiguously on the page, so a wrong answer
    // here is a substantive failure rather than a matter of phrasing.
    expect(get("parties").value, "parties").toMatch(/Northwind/i);
    expect(get("parties").value, "parties").toMatch(/Arbor/i);
    expect(get("term").value, "the 24-month initial term").toMatch(/24|twenty-four/i);
    expect(get("payment").value, "the quarterly fee").toMatch(/42,000|forty-two thousand/i);
    expect(get("termination").value, "the 90-day convenience notice").toMatch(/90|ninety/);
    expect(get("governingLaw").value, "New York law").toMatch(/New York/i);
    expect(get("liability").value, "the 12-month fee cap").toMatch(/cap|12|twelve/i);
    expect(get("unusual").value, "most-favoured pricing").toMatch(
      /most-favoured|most favoured|most-favored|most favored|audit/i,
    );

    // Every populated field is cited.
    for (const f of fields.filter((x) => x.value)) {
      expect(f.cites.length, `${f.label} is cited`).toBeGreaterThan(0);
    }
  });

  test("TC-05.5 every citation in the summary quotes text that is on its page", async ({
    request,
  }) => {
    const stored = await apiMessages(request, conversationId);
    const citations = stored.filter((m) => m.role === "assistant").at(-1)!.citations ?? [];
    expect(citations.length).toBeGreaterThan(0);

    for (const c of citations) {
      expect(c.page).toBeGreaterThanOrEqual(1);
      expect(c.page).toBeLessThanOrEqual(3);
      const pageText = (await apiPage(request, c.documentId, c.page)).text;
      expect(
        containsSpan(pageText, c.citedText),
        `source ${c.n} quotes text absent from page ${c.page}`,
      ).toBeTruthy();
    }
  });

  test("TC-05.6 the generation left an audit row and a cost row", async ({ request }) => {
    // NFR-9 and NFR-10: every generation is auditable and costed. Read through the
    // API the app exposes rather than the database, so the route is checked too.
    const res = await request.get(
      `${API}/api/audit?limit=50&conversationId=${conversationId}`,
    );
    expect(res.ok(), `GET /api/audit -> ${res.status()}`).toBeTruthy();
    const page = await res.json();
    expect(page.entries.length, "this conversation has audit entries").toBeGreaterThan(0);

    const summaryRow = page.entries.find((e: Record<string, unknown>) => e.feature === "summary");
    expect(summaryRow, "the summary turn is in the audit log").toBeTruthy();
    expect(summaryRow.outcome).toBe("ok");
    expect(String(summaryRow.modelId ?? ""), "the audit row names the model").not.toBe("");
    expect(summaryRow.documentIds, "and the document it read").toContain(documentId);

    const costs = await (await request.get(`${API}/api/audit/costs`)).json();
    expect(costs.totalCostUsd, "spend has been recorded").toBeGreaterThan(0);
    const purposes: string[] = (costs.byPurpose ?? []).map(
      (p: Record<string, string>) => p.purpose,
    );
    expect(
      purposes.some((p) => p.includes("summary")),
      `the summary call is costed under its own purpose; saw ${purposes.join(", ")}`,
    ).toBeTruthy();
  });
});
