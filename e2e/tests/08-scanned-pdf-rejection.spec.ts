import { expect, test } from "@playwright/test";
import {
  API,
  SCAN_FILENAME,
  SCAN_FIXTURE,
  addToScope,
  apiDocuments,
  createConversation,
  documentCard,
  removeFixtureUploads,
  ui,
} from "../helpers/app";

/**
 * TC-08 — FR-2: a scanned PDF is rejected, not transcribed.
 *
 * The fixture is a page rendered to a bitmap, so it carries pixels and zero
 * extractable characters. With OCR off this must come back as `failed` with a reason
 * the user can read, because a scan that indexes as empty text produces confident
 * answers about nothing — which the requirement calls the worse failure mode.
 *
 * Skipped when the service has OCR switched on, where transcribing it is the
 * documented behaviour instead.
 */
test.describe.serial("TC-08 a scanned PDF is rejected with its reason", () => {
  let documentId: string;

  test.beforeAll(async ({ request }) => {
    const health = await (await request.get(`${API}/api/health`)).json();
    const ocr: string = health.checks?.ocr ?? "";
    test.skip(
      !ocr.includes("disabled"),
      `OCR is enabled (${ocr}); a scan is transcribed rather than rejected`,
    );
    await removeFixtureUploads(request, SCAN_FILENAME);
  });

  test("TC-08.1 the upload is accepted, then marked not indexed", async ({ page, request }) => {
    await page.goto("/documents");
    const before = new Set((await apiDocuments(request)).map((d) => d.id));

    await page.locator("input[type=file]").setInputFiles(SCAN_FIXTURE);

    const card = documentCard(page, SCAN_FILENAME);
    await expect(card).toBeVisible({ timeout: 30_000 });
    // "Not indexed", not "Failed": the file is intact, the indexing is what did not
    // happen, and that is what tells the user whether to convert or to re-upload.
    await expect(card.getByText("Not indexed", { exact: true })).toBeVisible({
      timeout: 60_000,
    });

    const after = await apiDocuments(request);
    const stored = after.find((d) => !before.has(d.id))!;
    expect(stored).toBeTruthy();
    documentId = stored.id;
    expect(stored.status).toBe("failed");
  });

  test("TC-08.2 the rejection says why, in words a user can act on", async ({ page, request }) => {
    const doc = await (await request.get(`${API}/api/documents/${documentId}`)).json();
    expect(doc.failureReason, "the server explains the rejection").toBeTruthy();
    expect(String(doc.failureReason).length).toBeGreaterThan(20);
    expect(
      String(doc.failureReason),
      "the reason names the text layer or the scan",
    ).toMatch(/scan|text layer|no (?:extractable )?text|OCR/i);

    await page.goto("/documents");
    const card = documentCard(page, SCAN_FILENAME);
    await expect(card).toContainText(/scan|text layer|OCR/i);
  });

  test("TC-08.3 it cannot be used as a source", async ({ page, request }) => {
    // Not offered in the scope picker...
    await page.goto("/");
    await createConversation(page);
    await ui.scopeButton(page).click();
    await expect(
      page.getByRole("menuitem").filter({ hasText: SCAN_FILENAME.replace(/\.pdf$/, "") }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");

    // ...and refused by the API if asked for anyway, with a 409 that names it.
    const conv = await (
      await request.post(`${API}/api/conversations`, {
        data: { title: "E2E · scan scope", matterId: "m-001" },
      })
    ).json();
    const res = await request.post(`${API}/api/chat/message`, {
      data: {
        conversationId: conv.id,
        content: "What does this NDA say about the term?",
        scopedDocumentIds: [documentId],
      },
    });
    expect(res.status()).toBe(409);
    const problem = await res.json();
    expect(problem.code).toBe("document_not_ready");
    expect(problem.detail).toBeTruthy();
    await request.delete(`${API}/api/conversations/${conv.id}`);
  });

  test("TC-08.4 the viewer explains itself instead of showing an empty page", async ({
    page,
  }) => {
    await page.goto(`/documents/${documentId}`);
    // Whatever the wording, the viewer must not present a blank sheet as the document.
    await expect(page.getByText(/scan|text layer|not indexed|OCR/i).first()).toBeVisible();
  });

  test("TC-08.5 it can be deleted like any other document", async ({ page, request }) => {
    await page.goto("/documents");
    const card = documentCard(page, SCAN_FILENAME);
    await card.getByRole("button", { name: /^Delete / }).click();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(card).toHaveCount(0);
    expect((await apiDocuments(request)).some((d) => d.id === documentId)).toBeFalsy();
  });
});
