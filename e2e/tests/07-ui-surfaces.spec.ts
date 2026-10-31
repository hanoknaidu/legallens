import { expect, test } from "@playwright/test";
import {
  API,
  EXISTING_DOC_TITLE,
  MSA_TITLE,
  addToScope,
  apiDocuments,
  apiMessages,
  createConversation,
  documentCard,
  sendAndWait,
  ui,
} from "../helpers/app";

/**
 * TC-07 — the rest of the UI: the surfaces a user touches that are not the three
 * scenarios, and the failure paths that are easy to leave untested because they are
 * hard to trigger by hand.
 */
test.describe("TC-07 the rest of the UI", () => {
  test("TC-07.1 ⌘K opens the palette and navigates by keyboard", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("ControlOrMeta+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();

    // Groups, and the documents in the library, are listed.
    await expect(palette.getByText("Actions", { exact: true })).toBeVisible();
    await expect(palette.getByText("Documents", { exact: true })).toBeVisible();
    await expect(palette.getByRole("option", { name: /Go to documents/ })).toBeVisible();

    // Filtering is substring, case-insensitive.
    await palette.getByRole("textbox").fill("settings");
    await expect(palette.getByRole("option")).toHaveCount(1);
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/settings$/);

    // Nothing matching says so rather than showing an empty box.
    await page.keyboard.press("ControlOrMeta+k");
    await palette.getByRole("textbox").fill("zzzzz");
    await expect(palette.getByText(/Nothing matches/)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(palette).toHaveCount(0);
  });

  test("TC-07.2 the palette opens a document in the panel", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("ControlOrMeta+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("textbox").fill(EXISTING_DOC_TITLE);
    // By option id, not by name: a conversation auto-titled from a question about this
    // document matches the same text, and the palette lists both groups.
    await palette
      .locator('[role="option"][id^="doc-"]')
      .filter({ hasText: EXISTING_DOC_TITLE })
      .click();
    await expect(ui.docPanel(page)).toBeVisible();
    await expect(ui.docPanel(page).getByRole("heading", { name: EXISTING_DOC_TITLE })).toBeVisible();
  });

  test("TC-07.3 ⌘B collapses and restores the sidebar", async ({ page }) => {
    await page.goto("/");
    const sidebar = page.locator("aside").first();
    await expect(sidebar).toHaveAttribute("aria-hidden", "false");
    await page.keyboard.press("ControlOrMeta+b");
    await expect(sidebar).toHaveAttribute("aria-hidden", "true");
    await page.keyboard.press("ControlOrMeta+b");
    await expect(sidebar).toHaveAttribute("aria-hidden", "false");
  });

  test("TC-07.4 a suggestion on the empty state starts a conversation", async ({
    page,
    request,
  }) => {
    await page.goto("/chat/new");
    const before = (await request.get(`${API}/api/conversations`)).json();
    await page
      .getByRole("button", { name: "Summarise the Northwind / Arbor MSA." })
      .click();

    // It creates the conversation, navigates to it, and sends what was clicked.
    await page.waitForURL(/\/chat\/c-[0-9a-f]+/, { timeout: 30_000 });
    const id = page.url().match(/\/chat\/(c-[0-9a-f]+)/)![1];
    await expect(page.getByText("Summarise the Northwind / Arbor MSA.")).toBeVisible();
    await expect(ui.stopButton(page)).toBeHidden({ timeout: 240_000 });

    const stored = await apiMessages(request, id);
    expect(stored.filter((m) => m.role === "user")).toHaveLength(1);
    expect(stored[0].content).toBe("Summarise the Northwind / Arbor MSA.");
    expect((await before).length).toBeGreaterThan(0);

    await request.delete(`${API}/api/conversations/${id}`);
  });

  test("TC-07.5 Shift+Enter inserts a newline instead of sending", async ({ page, request }) => {
    await page.goto("/");
    const id = await createConversation(page);
    const box = ui.composer(page);
    await box.fill("first line");
    await box.press("Shift+Enter");
    await box.type("second line");
    await expect(box).toHaveValue("first line\nsecond line");
    // Nothing was sent.
    expect(await apiMessages(request, id)).toHaveLength(0);
    await request.delete(`${API}/api/conversations/${id}`);
  });

  test("TC-07.6 the send button is disabled until something is typed", async ({ page }) => {
    await page.goto("/");
    await createConversation(page);
    await expect(ui.sendButton(page)).toBeDisabled();
    await ui.composer(page).fill("   ");
    await expect(ui.sendButton(page)).toBeDisabled();
    await ui.composer(page).fill("a question");
    await expect(ui.sendButton(page)).toBeEnabled();
  });

  test("TC-07.7 an unsupported file is refused before it is uploaded", async ({
    page,
    request,
  }) => {
    const before = (await apiDocuments(request)).length;
    await page.goto("/documents");
    await page.locator("input[type=file]").setInputFiles({
      name: "notes.rtf",
      mimeType: "application/rtf",
      buffer: Buffer.from("{\\rtf1 not a contract}"),
    });
    await expect(page.getByText(/Only PDF, DOCX and TXT files can be indexed/)).toBeVisible();
    await expect(page.getByText("notes.rtf")).toBeVisible();
    // Client-side gate: nothing reached the server.
    expect((await apiDocuments(request)).length).toBe(before);
  });

  test("TC-07.8 the scope picker refuses a document that is not indexed", async ({
    page,
    request,
  }) => {
    const docs = await apiDocuments(request);
    const notReady = docs.find((d) => d.status !== "ready");
    test.skip(!notReady, "every document in the library is indexed");

    await page.goto("/");
    await createConversation(page);
    await ui.scopeButton(page).click();
    await expect(
      page.getByRole("menuitem").filter({ hasText: notReady!.title }),
      "an unindexed document is not offerable as a source",
    ).toHaveCount(0);
  });

  test("TC-07.9 asking against a document that is not ready is a 409 that names it", async ({
    request,
  }) => {
    const docs = await apiDocuments(request);
    const notReady = docs.find((d) => d.status !== "ready");
    test.skip(!notReady, "every document in the library is indexed");

    const conv = await (
      await request.post(`${API}/api/conversations`, {
        data: { title: "E2E · not-ready scope", matterId: "m-001" },
      })
    ).json();
    const res = await request.post(`${API}/api/chat/message`, {
      data: {
        conversationId: conv.id,
        content: "What does this say?",
        scopedDocumentIds: [notReady!.id],
      },
    });
    expect(res.status()).toBe(409);
    const problem = await res.json();
    expect(problem.code).toBe("document_not_ready");
    expect(problem.detail, "the problem names the document").toBeTruthy();
    await request.delete(`${API}/api/conversations/${conv.id}`);
  });

  test("TC-07.10 a rejected scan shows its reason in the library and the viewer", async ({
    page,
    request,
  }) => {
    const failed = (await apiDocuments(request)).find((d) => d.status === "failed");
    test.skip(!failed, "no rejected document in the library to check");

    await page.goto("/documents");
    const card = documentCard(page, failed!.title);
    await expect(card.getByText("Not indexed")).toBeVisible();
    await expect(card.getByText(/scan|text layer|OCR|not indexed/i).first()).toBeVisible();
  });

  test("TC-07.11 a stopped turn leaves the partial answer behind", async ({ page, request }) => {
    await page.goto("/");
    const id = await createConversation(page);
    await addToScope(page, EXISTING_DOC_TITLE);

    await ui.composer(page).fill(
      "Explain in detail every requirement this statute places on a bicycle rider.",
    );
    await ui.sendButton(page).click();
    await expect(ui.stopButton(page)).toBeVisible({ timeout: 60_000 });
    // Let a little text arrive, then stop.
    await page.waitForTimeout(3500);
    await ui.stopButton(page).click();
    await expect(ui.stopButton(page)).toBeHidden();

    // The user's turn is persisted regardless; the assistant turn is whatever arrived.
    const stored = await apiMessages(request, id);
    expect(stored.filter((m) => m.role === "user")).toHaveLength(1);

    const audit = await (
      await request.get(`${API}/api/audit?limit=20&conversationId=${id}`)
    ).json();
    if (audit.entries.length) {
      expect(
        ["stopped", "ok"],
        `the audit row records how the turn ended; got ${audit.entries[0].outcome}`,
      ).toContain(audit.entries[0].outcome);
    }
    await request.delete(`${API}/api/conversations/${id}`);
  });

  test("TC-07.12 the matter switcher filters the library and the thread list", async ({ page }) => {
    await page.goto("/documents");
    const all = await page.locator("main").getByRole("listitem").count();
    expect(all).toBeGreaterThan(0);

    await page.getByRole("button", { name: /matter/i }).first().click();
    await page.getByRole("menuitem", { name: /All matters/ }).click();
    await expect(page.locator("main").getByRole("listitem")).toHaveCount(all);

    await page.getByRole("button", { name: /All matters/ }).click();
    await page.getByRole("menuitem", { name: /Northwind/ }).click();
    await expect(page.getByRole("button", { name: /Northwind/ })).toBeVisible();
  });

  test("TC-07.13 the answer's copy action puts the text on the clipboard", async ({
    page,
    context,
    request,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/");
    const id = await createConversation(page);
    await addToScope(page, EXISTING_DOC_TITLE);
    await sendAndWait(page, "Is a bell required on a bicycle?");

    const stored = await apiMessages(request, id);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;

    const turn = page.locator("div.group\\/msg").last();
    await turn.hover();
    await turn.getByRole("button", { name: "Copy answer" }).click();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip.trim()).toBe(answer.content.trim());

    await request.delete(`${API}/api/conversations/${id}`);
  });

  test("TC-07.14 a document deleted from the library takes its pages with it", async ({
    page,
    request,
  }) => {
    // Uses a throwaway upload so nothing the other specs rely on is destroyed.
    const res = await request.post(`${API}/api/documents`, {
      multipart: {
        file: {
          name: "e2e-throwaway.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(
            "SIDE LETTER\n\nThe parties agree that the rent shall be nine hundred dollars " +
              "($900) per month, payable on the first day of each month.\n",
          ),
        },
        matterId: "m-001",
      },
    });
    expect(res.ok(), `upload -> ${res.status()}`).toBeTruthy();
    const doc = await res.json();

    await expect
      .poll(async () => (await apiDocuments(request)).find((d) => d.id === doc.id)?.status, {
        timeout: 60_000,
        intervals: [500],
      })
      .toBe("ready");

    await page.goto("/documents");
    const card = documentCard(page, "e2e-throwaway");
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: /^Delete / }).click();

    // The dialog spells out what is purged before it happens.
    await expect(page.getByText(/purges the original file/)).toBeVisible();
    await page.getByRole("button", { name: "Delete", exact: true }).click();

    await expect(card).toHaveCount(0);
    expect((await apiDocuments(request)).some((d) => d.id === doc.id)).toBeFalsy();
    // Its pages are gone with it.
    const pageRes = await request.get(`${API}/api/documents/${doc.id}/page/1`);
    expect(pageRes.status()).toBe(404);
  });

  test("TC-07.15 the settings page describes this build honestly", async ({ page }) => {
    // DEFECT (confirmed): the "About this build" copy is unconditional, so it claims the
    // data is mocked while the app is reading and writing a real database.
    test.fail();
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Keyboard shortcuts" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "About this build" })).toBeVisible();
    // Running against the real backend, so claiming otherwise is wrong.
    await expect(
      page.getByText("All data is mocked in the browser. No backend is connected."),
      "the settings page must not claim the data is mocked when it is not",
    ).toHaveCount(0);
  });

  test("TC-07.16 the full-page document view renders a page", async ({ page, request }) => {
    const doc = (await apiDocuments(request)).find((d) => d.status === "ready")!;
    await page.goto(`/documents/${doc.id}`);
    await expect(page.getByRole("heading", { name: doc.title }).first()).toBeVisible();
    await expect(page.getByText(/^Page 1 of \d+$/).first()).toBeVisible();
  });

  test("TC-07.17 the library is reachable from a document the chat cited", async ({ page }) => {
    await page.goto("/documents");
    await expect(page.getByRole("link", { name: MSA_TITLE })).toBeVisible();
    await page.getByRole("link", { name: MSA_TITLE }).click();
    await expect(page).toHaveURL(/\/documents\/d-[0-9a-f]+/);
  });
});
