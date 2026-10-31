import { expect, test } from "@playwright/test";
import { API, apiDocuments, documentCard, ui } from "../helpers/app";

/**
 * TC-01 — the shell, before anything is generated.
 *
 * These are the assertions that explain every later failure: if the app is serving
 * fixtures, or the backend banner is up, nothing below this file means anything.
 */
test.describe("TC-01 shell and navigation", () => {
  test("TC-01.1 the app loads, redirects to a new chat, and reports no backend trouble", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

    await page.goto("/");
    await expect(page).toHaveURL(/\/chat\/new$/);

    await expect(page.getByRole("heading", { level: 2 })).toContainText(
      "Ask, summarise or draft",
    );
    // The banner renders only when health is degraded or unreachable.
    await expect(page.getByText("Backend unreachable.")).toHaveCount(0);
    await expect(page.getByText("Running degraded.")).toHaveCount(0);
    expect(consoleErrors, consoleErrors.join("\n")).toHaveLength(0);
  });

  test("TC-01.2 the mock layer is not serving this run", async ({ page }) => {
    const seen: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/")) seen.push(r.url());
    });
    await page.goto("/documents");
    await expect(page.getByRole("heading", { name: /documents?$/ })).toBeVisible();

    // MSW intercepts in-page, so a mocked run issues no network request at all.
    expect(seen.some((u) => u.includes("/api/documents"))).toBeTruthy();
    const mswWorker = await page.evaluate(() => "serviceWorker" in navigator
      ? navigator.serviceWorker.getRegistrations().then((r) => r.length)
      : 0);
    expect(mswWorker, "an MSW service worker is registered").toBe(0);
  });

  test("TC-01.3 the sidebar reaches documents, settings and back to a chat", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Documents" }).click();
    await expect(page).toHaveURL(/\/documents$/);

    await page.getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("heading", { name: "Keyboard shortcuts" })).toBeVisible();

    await ui.newChatButton(page).click();
    await expect(page).toHaveURL(/\/chat\/c-[0-9a-f]+/);
  });

  test("TC-01.4 the documents page lists what the API lists, with matching status", async ({
    page,
    request,
  }) => {
    const docs = await apiDocuments(request);
    expect(docs.length, "the database has documents to test against").toBeGreaterThan(0);

    await page.goto("/documents");
    await expect(page.getByRole("heading", { name: `${docs.length} documents` })).toBeVisible();

    for (const d of docs) {
      const card = documentCard(page, d.title);
      await expect(card).toBeVisible();
      const label = { ready: "Ready", processing: "Indexing", queued: "Queued", failed: "Not indexed" }[
        d.status
      ]!;
      await expect(card.getByText(label, { exact: true }).first()).toBeVisible();
      await expect(card).toContainText(`${d.pages} pages`);
    }
  });

  test("TC-01.5 an unknown route falls back to a new chat", async ({ page }) => {
    await page.goto("/does-not-exist");
    await expect(page).toHaveURL(/\/chat\/new$/);
  });

  test("TC-01.6 health reports every subsystem as usable", async ({ request }) => {
    const res = await request.get(`${API}/api/health`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("ok");
    for (const [name, state] of Object.entries(body.checks as Record<string, string>)) {
      expect(state.startsWith("ok"), `${name}: ${state}`).toBeTruthy();
    }
  });
});
