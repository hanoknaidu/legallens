import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  API,
  EXISTING_DOC_TITLE,
  addToScope,
  createConversation,
  ui,
} from "../helpers/app";

/**
 * Produces one screenshot per defect in the test report.
 *
 * Each case finds its own evidence rather than depending on a particular database: it
 * either reproduces the defect live, or locates a turn that already exhibits it and
 * opens the screen that shows it. A case that can find nothing fails loudly instead of
 * writing a picture of something else.
 */

const OUT = path.resolve(__dirname, "../findings");
fs.mkdirSync(OUT, { recursive: true });

const shot = (page: Page, name: string) =>
  page.screenshot({ path: path.join(OUT, `${name}.png`) });

interface Msg {
  id: string;
  role: string;
  content: string;
  intent?: string | null;
  citations?: { n: number; documentId: string; page: number; citedText: string }[] | null;
  summary?: unknown;
  draft?: { title: string; body: string } | null;
  abstained?: boolean | null;
}

async function conversations(ctx: APIRequestContext) {
  return (await (await ctx.get(`${API}/api/conversations`)).json()) as {
    id: string;
    title: string;
    updatedAt: string;
  }[];
}

async function messages(ctx: APIRequestContext, id: string): Promise<Msg[]> {
  const res = await ctx.get(`${API}/api/conversations/${id}/messages`);
  return res.ok() ? await res.json() : [];
}

const norm = (s: string) =>
  s.replace(/\s+/g, " ").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').trim();

/** Walks the transcript history for the first turn matching a predicate. */
async function findTurn(
  ctx: APIRequestContext,
  match: (m: Msg, pageText: (d: string, p: number) => Promise<string | null>) => Promise<boolean>,
) {
  const cache = new Map<string, string | null>();
  const pageText = async (d: string, p: number) => {
    const key = `${d}#${p}`;
    if (!cache.has(key)) {
      const res = await ctx.get(`${API}/api/documents/${d}/page/${p}`);
      cache.set(key, res.ok() ? (await res.json()).text : null);
    }
    return cache.get(key)!;
  };

  for (const c of await conversations(ctx)) {
    for (const m of await messages(ctx, c.id)) {
      if (m.role !== "assistant") continue;
      if (await match(m, pageText)) return { conversationId: c.id, message: m, title: c.title };
    }
  }
  return null;
}

test.describe.serial("defect evidence", () => {
  test("D1 — the row menu navigates away from the page you were on", async ({
    page,
    request,
  }) => {
    const created = await request.post(`${API}/api/conversations`, {
      data: { title: "D1 · menu click-through demo", matterId: "m-001" },
    });
    const id = (await created.json()).id as string;

    await page.goto("/documents");
    await expect(page.getByRole("heading", { name: /documents$/ })).toBeVisible();
    await shot(page, "D1-a-before-document-library");

    const row = ui.conversationRow(page, id);
    await row.hover();
    await row.getByRole("button", { name: /^Actions for / }).click();
    await expect(page.getByRole("menuitem", { name: "Rename" })).toBeVisible();
    await shot(page, "D1-b-menu-open-still-on-documents");

    await page.getByRole("menuitem", { name: "Rename" }).click();
    await expect(page.getByRole("textbox", { name: "Conversation title" })).toBeVisible();
    await page.waitForTimeout(500);
    // The library is gone: the app is now inside the conversation the menu belonged to.
    expect(page.url(), "the route changed on a menu selection").toContain(id);
    await shot(page, "D1-c-after-rename-navigated-into-conversation");

    await page.keyboard.press("Escape");
    await request.delete(`${API}/api/conversations/${id}`);
  });

  test("D2 (fixed) — an answer whose claims carry citation chips", async ({
    page,
    request,
  }) => {
    const hit = await findTurn(request, async (m) =>
      m.intent === "ask" &&
      (m.citations?.length ?? 0) > 0 &&
      (m.content ?? "").includes("{{cite:"),
    );
    expect(hit, "an `ask` turn carrying inline markers").toBeTruthy();

    await page.goto(`/chat/${hit!.conversationId}`);
    const chips = ui.citationChips(page);
    await expect(chips.first()).toBeVisible();
    // Hovering previews the passage, which is the half of the feature a reader uses
    // before deciding whether to open the page.
    await chips.first().hover();
    await expect(page.getByRole("button", { name: /^Open at page \d+$/ })).toBeVisible();
    await shot(page, "D2-fixed-answer-carries-citation-chips");
  });

  test("D3 — a stop in one tab empties the other tab's answer", async ({
    context,
    request,
  }) => {
    const a = await context.newPage();
    const b = await context.newPage();
    await a.goto("/");
    await b.goto("/");

    const convA = await createConversation(a);
    const convB = await createConversation(b);

    // A summary, deliberately: it is the longest turn the app produces and it emits no
    // text until the very end, so there is a wide window in which the cancel can land.
    // A short question finishes before the stop is ever observed, which is why this
    // defect is easy to miss by hand.
    await b.bringToFront();
    await addToScope(b, EXISTING_DOC_TITLE);
    await ui.composer(b).fill(`Summarise ${EXISTING_DOC_TITLE}.`);
    await ui.sendButton(b).click();
    await expect(ui.stopButton(b)).toBeVisible({ timeout: 60_000 });
    await b.waitForTimeout(1500);
    await shot(b, "D3-a-tab-B-answer-in-flight");

    await a.bringToFront();
    await addToScope(a, EXISTING_DOC_TITLE);
    await ui.composer(a).fill("List every lighting requirement in this statute.");
    await ui.sendButton(a).click();
    await expect(ui.stopButton(a)).toBeVisible({ timeout: 60_000 });
    await a.waitForTimeout(1000);
    expect(await ui.stopButton(b).isVisible(), "tab B is still generating").toBeTruthy();
    // Tab A stops its own turn. Nothing here refers to tab B.
    await ui.stopButton(a).click();
    await shot(a, "D3-b-tab-A-pressed-stop-on-its-own-turn");

    await b.bringToFront();
    await expect(ui.stopButton(b)).toBeHidden({ timeout: 240_000 });
    await b.reload();
    await b.waitForTimeout(1200);
    await shot(b, "D3-c-tab-B-answer-cancelled-and-empty");

    const answerB = (await messages(request, convB)).filter((m) => m.role === "assistant").at(-1);
    const produced =
      (answerB?.content ?? "").trim().length + (answerB?.summary ? 1 : 0);
    expect(
      produced,
      "tab B's turn was emptied by tab A's stop — if this is non-zero the cancel " +
        "landed after tab B had finished, so run it again",
    ).toBe(0);

    await request.delete(`${API}/api/conversations/${convA}`);
    await request.delete(`${API}/api/conversations/${convB}`);
    await a.close();
    await b.close();
  });

  test("D4 — a citation spanning two pages highlights nothing", async ({ page, request }) => {
    const hit = await findTurn(request, async (m, pageText) => {
      for (const c of m.citations ?? []) {
        const text = await pageText(c.documentId, c.page);
        if (text && !norm(text).includes(norm(c.citedText))) return true;
      }
      return false;
    });
    expect(hit, "a turn carrying a citation whose text is not on its page").toBeTruthy();

    const offender = (await Promise.all(
      (hit!.message.citations ?? []).map(async (c) => {
        const res = await request.get(`${API}/api/documents/${c.documentId}/page/${c.page}`);
        if (!res.ok()) return null;
        const text = (await res.json()).text as string;
        return norm(text).includes(norm(c.citedText)) ? null : c;
      }),
    )).find(Boolean)!;

    // Opened by clicking the chip, not by URL: `?doc=&page=` opens the panel at a page
    // but sets no *active* citation, and the viewer only reports a missing passage when
    // it has a span it was asked to mark.
    await page.goto(`/chat/${hit!.conversationId}`);
    const chip = page
      .getByRole("button", { name: new RegExp(`^Source ${offender.n}:`) })
      .first();
    await expect(chip, "the offending source is chipped in the turn").toBeVisible();
    await chip.click();

    const panel = ui.docPanel(page);
    await expect(panel).toBeVisible();
    await expect(panel.getByText(`Showing source ${offender.n}`)).toBeVisible();
    await expect(
      page.getByText("The cited passage was not found in this page's text."),
    ).toBeVisible();
    await page.waitForTimeout(500);
    await shot(page, "D4-citation-passage-not-found-on-its-page");
  });

  test("D5 (fixed) — a turn that produced nothing now says so", async ({ page, request }) => {
    const hit = await findTurn(
      request,
      async (m) =>
        !m.abstained &&
        !m.summary &&
        !m.draft &&
        (m.content ?? "").trim().length === 0,
    );
    test.skip(!hit, "no empty turn in the database to photograph");

    await page.goto(`/chat/${hit!.conversationId}`);
    await expect(page.getByText("This answer did not complete.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask again" })).toBeVisible();
    await page.waitForTimeout(600);
    await shot(page, "D5-fixed-incomplete-turn-explains-itself");
  });

  test("D6 — the settings page claims the data is mocked", async ({ page }) => {
    await page.goto("/settings");
    await expect(
      page.getByText("All data is mocked in the browser. No backend is connected."),
    ).toBeVisible();
    await shot(page, "D6-settings-claims-data-is-mocked");
  });

  test("D7 — a draft title cut off mid-word", async ({ page, request }) => {
    const hit = await findTurn(
      request,
      async (m) => !!m.draft && /\s\S{1,2}$/.test(m.draft.title),
    );
    expect(hit, "a draft whose title was truncated mid-word").toBeTruthy();

    await page.goto(`/chat/${hit!.conversationId}`);
    await expect(page.getByRole("heading", { name: hit!.message.draft!.title })).toBeVisible();
    await page.waitForTimeout(600);
    await shot(page, "D7-draft-title-truncated-mid-word");
  });
});
