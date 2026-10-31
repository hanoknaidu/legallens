import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  API,
  MSA_FIXTURE,
  MSA_TITLE,
  addToScope,
  apiDocuments,
  apiMessages,
  apiPage,
  containsSpan,
  createConversation,
  sendAndWait,
  ui,
} from "../helpers/app";

/**
 * TC-06 — scenario 3: ask for a memo and check what comes back is a draft, not prose.
 *
 * FR-16/FR-17 are the point of this one. An unreviewed draft has to be *visibly*
 * unreviewed: a banner that says so, `[ATTORNEY INPUT REQUIRED]` where a fact is
 * missing, and the same notice inside the exported file — because the file is what
 * leaves the building.
 */
test.describe.serial("TC-06 draft a memo", () => {
  let conversationId: string;
  let documentId: string;

  test.beforeAll(async ({ request }) => {
    // TC-05 uploads this through the UI. Uploaded here too if this spec is run alone,
    // so the draft always has a contract to draft from.
    const existing = (await apiDocuments(request)).find((d) => d.title === MSA_TITLE);
    if (existing?.status === "ready") {
      documentId = existing.id;
      return;
    }
    const res = await request.post(`${API}/api/documents`, {
      multipart: {
        file: { name: path.basename(MSA_FIXTURE), mimeType: "application/pdf", buffer: fs.readFileSync(MSA_FIXTURE) },
        matterId: "m-001",
      },
    });
    expect(res.ok(), `upload -> ${res.status()}`).toBeTruthy();
    documentId = (await res.json()).id;
    await expect
      .poll(
        async () => (await apiDocuments(request)).find((d) => d.id === documentId)?.status,
        { timeout: 120_000, intervals: [1000] },
      )
      .toBe("ready");
  });

  test("TC-06.1 a memo request is routed to the draft workflow", async ({ page, request }) => {
    await page.goto("/");
    conversationId = await createConversation(page);
    await addToScope(page, MSA_TITLE);

    await sendAndWait(
      page,
      "Draft a memo to the client on their termination rights under this agreement, " +
        "including the notice periods and what triggers a termination for cause.",
    );

    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(answer.intent, "the router chose `draft`").toBe("draft");
    expect(answer.draft, "a draft payload came back, not prose").toBeTruthy();
    expect(answer.draft!.title.length, "the draft is titled").toBeGreaterThan(5);
    expect(answer.draft!.body.length, "the memo has a body").toBeGreaterThan(400);

    await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  });

  test("TC-06.2 the draft is presented as unreviewed, with its gaps marked", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const draft = stored.filter((m) => m.role === "assistant").at(-1)!.draft!;

    // FR-17: the warning is not optional and not subtle.
    await expect(
      page.getByText("Unreviewed draft — requires attorney review"),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: draft.title })).toBeVisible();

    const markers = (draft.body.match(/\[ATTORNEY INPUT REQUIRED[^\]]*\]/g) ?? []).length;
    await expect(page.getByText(new RegExp(`^${markers} sections? needs? attorney input`))).toBeVisible();
    // Each marker is rendered as a visible mark, not swallowed by the markdown.
    if (markers > 0) {
      await expect(page.locator("mark").filter({ hasText: "ATTORNEY INPUT REQUIRED" })).toHaveCount(
        markers,
      );
    }
  });

  test("TC-06.3 the memo's assertions are grounded in the agreement", async ({ request }) => {
    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    const citations = answer.citations ?? [];

    expect(citations.length, "the draft cites the document it drafted from").toBeGreaterThan(0);
    for (const c of citations) {
      expect(c.documentId, "nothing outside the scope was cited").toBe(documentId);
      const pageText = (await apiPage(request, c.documentId, c.page)).text;
      expect(
        containsSpan(pageText, c.citedText),
        `source ${c.n} quotes text absent from page ${c.page}`,
      ).toBeTruthy();
    }

    // The substance: the memo should carry the two notice periods that are on the page.
    const body = answer.draft!.body;
    expect(body, "the 90-day convenience notice").toMatch(/\b(90|ninety)\b/i);
    expect(body, "the 30-day cure period").toMatch(/\b(30|thirty)\b/i);
  });

  test("TC-06.3b the memo's citations are reachable from inside the memo", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(answer.citations?.length ?? 0).toBeGreaterThan(0);

    // Same contract as an answer: `DraftDocument` renders the body through `Markdown`
    // with the citation list, and `Markdown` builds chips from `{{cite:n}}` markers. A
    // memo with citations attached but no markers in its body gives the reviewing
    // attorney a source count and no way to reach a source from the sentence it
    // supports — which is the whole point of the feature.
    expect(
      answer.draft!.body,
      "the draft body carries {{cite:n}} markers for its citations",
    ).toMatch(/\{\{cite:\d+\}\}/);
    await expect(
      ui.citationChips(page),
      "at least one citation chip is rendered inside the memo",
    ).not.toHaveCount(0);
  });

  test("TC-06.3c the draft title is a title, not the request cut off mid-word", async ({
    request,
  }) => {
    // DEFECT (confirmed): `title_for()` caps with a plain 120-character slice.
    test.fail();
    const stored = await apiMessages(request, conversationId);
    const title = stored.filter((m) => m.role === "assistant").at(-1)!.draft!.title;

    // `title_for()` caps the request at 120 characters with a plain slice, so a long
    // request ends the rendered heading — and the exported file's title — part way
    // through a word.
    expect(title.length, "the title is capped").toBeLessThanOrEqual(120);
    expect(
      title,
      `the title should not be truncated mid-word: ...${title.slice(-40)}`,
    ).not.toMatch(/\s\S{1,2}$/);
  });

  test("TC-06.4 the draft exports as .docx with the FR-17 notice inside the file", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const messageId = stored.filter((m) => m.role === "assistant").at(-1)!.id;

    // Through the UI, so the menu, the blob and the filename are all exercised.
    const download = page.waitForEvent("download", { timeout: 60_000 });
    await page.getByRole("button", { name: "Export" }).click();
    await page.getByRole("menuitem", { name: "Word document (.docx)" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/\.docx$/);
    const saved = await file.path();
    expect(saved, "the browser received bytes").toBeTruthy();

    // And the same export read directly, so the notice can be checked in the content.
    const res = await request.post(`${API}/api/messages/${messageId}/export`, {
      data: { format: "markdown" },
    });
    expect(res.ok(), `export -> ${res.status()}`).toBeTruthy();
    const md = await res.text();
    expect(md.toLowerCase(), "the file says it is unreviewed").toMatch(
      /unreviewed|attorney review|not legal advice/,
    );
    expect(res.headers()["content-disposition"]).toContain("attachment");
  });

  test("TC-06.5 the draft survives a reload with its banner and markers", async ({ page }) => {
    await page.goto(`/chat/${conversationId}`);
    await page.reload();
    await expect(page.getByText("Unreviewed draft — requires attorney review")).toBeVisible();
    await expect(page.getByText("Draft", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Export" })).toBeEnabled();
  });

  test("TC-06.6 Copy puts the draft body on the clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(`/chat/${conversationId}`);
    await page.getByRole("button", { name: "Copy" }).click();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.length, "something was copied").toBeGreaterThan(200);
  });
});
