import path from "node:path";
import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";

export const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";

/** How long one generated turn may take before the test gives up on it. */
export const TURN_TIMEOUT = 240_000;
/** How long a document may stay in `queued`/`processing`. */
export const INGEST_TIMEOUT = 120_000;

export const MSA_FIXTURE = path.resolve(__dirname, "../fixtures/northwind-arbor-msa.pdf");
export const MSA_FILENAME = "northwind-arbor-msa.pdf";
/** An image-only PDF: pixels, no text layer. FR-2 requires it to be rejected. */
export const SCAN_FIXTURE = path.resolve(__dirname, "../fixtures/scanned-nda.pdf");
export const SCAN_FILENAME = "scanned-nda.pdf";
/** The display title the backend derives from the filename: hyphens become spaces. */
export const MSA_TITLE = "northwind arbor msa";
/** Already in the database from manual use; the "pick an existing document" scenario. */
export const EXISTING_DOC_TITLE = "New Jersey Bicycle Laws";

// ---------------------------------------------------------------------------
// Locators. Named so a failure message says what was being looked for.
// ---------------------------------------------------------------------------

export const ui = {
  newChatButton: (p: Page) => p.getByRole("button", { name: /^New chat/ }),
  composer: (p: Page) => p.getByRole("textbox", { name: "Message" }),
  sendButton: (p: Page) => p.getByRole("button", { name: "Send message" }),
  stopButton: (p: Page) => p.getByRole("button", { name: "Stop generating" }),
  scopeButton: (p: Page) => p.getByRole("button", { name: "Add documents to scope" }),
  conversationNav: (p: Page) => p.getByRole("navigation", { name: "Conversations" }),
  /**
   * A sidebar row, found by the conversation it links to.
   *
   * By id rather than by title on purpose: "New conversation" is the placeholder every
   * unnamed thread carries, so a title is not a unique handle and a test that used one
   * would act on whichever row happened to sort first.
   */
  conversationRow: (p: Page, id: string) =>
    ui.conversationNav(p).locator(`a[href="/chat/${id}"]`),
  conversationLink: (p: Page, title: string) =>
    ui.conversationNav(p).getByRole("link", { name: title, exact: false }),
  conversationMenu: (p: Page, title: string) =>
    p.getByRole("button", { name: `Actions for ${title}` }),
  topBarTitle: (p: Page) => p.locator("h1 button[title='Rename conversation']"),
  searchConversations: (p: Page) => p.getByRole("textbox", { name: "Search conversations" }),
  /**
   * The source panel, in whichever form the viewport puts it in: a docked column above
   * 1440px, a modal overlay below. Both are the same component and the same assertions
   * apply, so the locator accepts either rather than pinning the test to one layout.
   */
  docPanel: (p: Page) =>
    p.locator(
      '[role="complementary"][aria-label="Document source"], [role="dialog"][aria-label="Document source"]',
    ),
  citationChips: (p: Page) => p.getByRole("button", { name: /^Source \d+:/ }),
  summaryCard: (p: Page) => p.locator("div", { has: p.getByRole("heading", { name: "Structured summary" }) }).first(),
  intentBadge: (p: Page, label: "Ask" | "Summary" | "Draft") =>
    p.getByText(label, { exact: true }),
};

// ---------------------------------------------------------------------------
// Flows
// ---------------------------------------------------------------------------

/**
 * Creates a conversation through the sidebar and returns its id.
 *
 * The id comes from the URL rather than from the API response: what matters is that
 * the UI navigated to the thread it just made, which is the part a user notices.
 */
export async function createConversation(page: Page): Promise<string> {
  await ui.newChatButton(page).click();
  await page.waitForURL(/\/chat\/c-[0-9a-f]+/, { timeout: 20_000 });
  const id = page.url().match(/\/chat\/(c-[0-9a-f]+)/)![1];
  await expect(ui.composer(page)).toBeVisible();
  return id;
}

/** Adds a document to the composer's scope by its visible title. */
export async function addToScope(page: Page, title: string) {
  await ui.scopeButton(page).click();
  const item = page.getByRole("menuitem").filter({ hasText: title });
  await expect(item).toBeVisible();
  await item.click();
  // The menu closes on select; the chip is the confirmation the user sees.
  await expect(page.getByRole("button", { name: `Remove ${title} from scope` })).toBeVisible();
}

/**
 * Types a message, sends it, and waits for the generated turn to finish.
 *
 * "Finished" is read off the composer: it shows a stop button for exactly as long as
 * `useChat` holds a streaming turn, so the button appearing and then going away is
 * the same signal the user is watching.
 */
export async function sendAndWait(page: Page, text: string, timeout = TURN_TIMEOUT) {
  await ui.composer(page).fill(text);
  await ui.sendButton(page).click();
  await expect(ui.stopButton(page)).toBeVisible({ timeout: 60_000 });
  await expect(ui.stopButton(page)).toBeHidden({ timeout });
}

/** Waits for a turn already in flight (one sent from `/chat/new`, say). */
export async function waitForTurn(page: Page, timeout = TURN_TIMEOUT) {
  await expect(ui.stopButton(page)).toBeVisible({ timeout: 60_000 });
  await expect(ui.stopButton(page)).toBeHidden({ timeout });
}

/** The transcript column, excluding the sidebar and the top bar. */
export function transcript(page: Page): Locator {
  return page.locator("main");
}

/** One assistant turn. */
export function assistantTurns(page: Page): Locator {
  return transcript(page).locator("div.group\\/msg");
}

/** The last assistant turn in the transcript. */
export function lastAssistantTurn(page: Page): Locator {
  return assistantTurns(page).last();
}

/**
 * A user's own message bubble.
 *
 * Scoped, and matched on the bubble itself: the sidebar row and the editable top-bar
 * title both repeat the first question of a thread word for word, so an unscoped text
 * match finds three elements and fails on strictness rather than on behaviour.
 */
export function userBubble(page: Page, text: string): Locator {
  return transcript(page).locator("div.bg-brand-600").filter({ hasText: text });
}

/** Opens the "…" menu on one sidebar row. */
async function openRowMenu(page: Page, conversationId: string) {
  const row = ui.conversationRow(page, conversationId);
  await expect(row).toBeVisible();
  await row.hover();
  await row.getByRole("button", { name: /^Actions for / }).click();
}

export async function renameFromSidebar(page: Page, conversationId: string, to: string) {
  await openRowMenu(page, conversationId);
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const field = page.getByRole("textbox", { name: "Conversation title" });
  await expect(field).toBeVisible();
  await field.fill(to);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(field).toBeHidden();
}

export async function deleteFromSidebar(page: Page, conversationId: string) {
  await openRowMenu(page, conversationId);
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await expect(page.getByText("Delete conversation?")).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
}

/** Uploads a file through the Documents page and waits for it to reach `ready`. */
export async function uploadDocument(page: Page, filePath: string, title: string) {
  await page.goto("/documents");
  await page.locator("input[type=file]").setInputFiles(filePath);
  const card = documentCard(page, title);
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card.getByText("Ready")).toBeVisible({ timeout: INGEST_TIMEOUT });
}

/**
 * One card in the document library.
 *
 * Scoped to `main` because the sidebar's conversation list is also a list of list
 * items, and a conversation named after a document matches both.
 */
export function documentCard(page: Page, title: string): Locator {
  return page.locator("main").getByRole("listitem").filter({ hasText: title });
}

// ---------------------------------------------------------------------------
// API reads, used only to check that what the UI showed is what was stored
// ---------------------------------------------------------------------------

export async function apiMessages(ctx: APIRequestContext, conversationId: string) {
  const res = await ctx.get(`${API}/api/conversations/${conversationId}/messages`);
  expect(res.ok(), `GET messages ${conversationId} -> ${res.status()}`).toBeTruthy();
  return (await res.json()) as StoredMessage[];
}

export async function apiConversations(ctx: APIRequestContext) {
  const res = await ctx.get(`${API}/api/conversations`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as { id: string; title: string; updatedAt: string }[];
}

/**
 * Removes every copy of the upload fixture.
 *
 * The library has no notion of "the same file again" — a second upload of the same PDF
 * is a second document with the same title — so a spec that uploads has to start from a
 * known state or its own assertions become ambiguous on the second run.
 */
export async function removeFixtureUploads(
  ctx: APIRequestContext,
  filename: string = MSA_FILENAME,
) {
  for (const doc of await apiDocuments(ctx)) {
    if (doc.filename === filename) {
      const res = await ctx.delete(`${API}/api/documents/${doc.id}`);
      expect([204, 404], `DELETE ${doc.id} -> ${res.status()}`).toContain(res.status());
    }
  }
  // Read it back. The row goes inside the same transaction, but the object store and
  // the vector index are reconciled through `deletion_jobs`, and a spec that counts
  // documents either side of an upload needs the list to have settled first.
  await expect
    .poll(async () => (await apiDocuments(ctx)).filter((d) => d.filename === filename).length, {
      timeout: 15_000,
      intervals: [250],
    })
    .toBe(0);
}

export async function apiDocuments(ctx: APIRequestContext) {
  const res = await ctx.get(`${API}/api/documents`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as {
    id: string;
    title: string;
    filename: string;
    status: string;
    pages: number;
    textSource?: string;
  }[];
}

export async function apiPage(ctx: APIRequestContext, documentId: string, page: number) {
  const res = await ctx.get(`${API}/api/documents/${documentId}/page/${page}`);
  expect(res.ok(), `GET page ${page} of ${documentId} -> ${res.status()}`).toBeTruthy();
  return (await res.json()) as { page: number; text: string };
}

export interface StoredCitation {
  n: number;
  documentId: string;
  documentTitle: string;
  page: number;
  citedText: string;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  intent?: "ask" | "summary" | "draft" | null;
  grounded?: boolean | null;
  abstained?: boolean | null;
  citations?: StoredCitation[] | null;
  summary?: { key: string; label: string; value: string | null; cites: number[] }[] | null;
  draft?: { title: string; body: string } | null;
  scopedDocumentIds?: string[] | null;
}

/** Whitespace-tolerant containment, matching how the viewer locates a cited span. */
export function containsSpan(haystack: string, span: string): boolean {
  const norm = (s: string) => s.replace(/\s+/g, " ").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').trim();
  return norm(haystack).includes(norm(span));
}
