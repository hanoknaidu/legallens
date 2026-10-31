import { expect, test } from "@playwright/test";
import {
  EXISTING_DOC_TITLE,
  addToScope,
  apiConversations,
  apiMessages,
  apiPage,
  containsSpan,
  createConversation,
  assistantTurns,
  lastAssistantTurn,
  sendAndWait,
  ui,
  userBubble,
} from "../helpers/app";

/**
 * TC-03 — ask a question, and verify the answer is traceable.
 *
 * The product's only hard promise is that a cited claim can be checked, so the
 * assertions go all the way through: the chip exists, it names a page, that page's
 * text really contains the quoted span (fetched from the API, independently of the
 * UI), and clicking the chip opens the panel at that page with the span marked.
 *
 * That last step is the item `docs/integration-TODO.md` records as needing "a human
 * at a browser". This is that human.
 */
test.describe.serial("TC-03 ask and citations", () => {
  let conversationId: string;

  test("TC-03.1 a scoped question returns a grounded, cited answer", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    conversationId = await createConversation(page);
    await addToScope(page, EXISTING_DOC_TITLE);

    await sendAndWait(
      page,
      "What are the bicycle helmet requirements, and who do they apply to?",
    );

    // The question is in the transcript with the document it was scoped to.
    await expect(userBubble(page, "What are the bicycle helmet requirements")).toBeVisible();

    const turn = lastAssistantTurn(page);
    await expect(turn).toBeVisible();
    // `ask`, not a summary or a draft.
    await expect(turn.getByText("Ask", { exact: true })).toBeVisible();
    // Grounded, with at least one source counted in the header badge.
    await expect(turn.getByText(/\d+ sources?$/)).toBeVisible();
    await expect(page.getByText("Not found in the documents in scope.")).toHaveCount(0);

    // Now the same turn as the server stored it.
    const stored = await apiMessages(request, conversationId);
    const assistant = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(assistant.intent).toBe("ask");
    expect(assistant.abstained ?? false).toBe(false);
    expect(assistant.grounded).toBe(true);
    expect(assistant.content.length, "the answer is not empty").toBeGreaterThan(40);
    expect(assistant.citations?.length ?? 0, "at least one citation").toBeGreaterThan(0);

    // The user turn kept the scope it was asked under.
    const user = stored.filter((m) => m.role === "user").at(-1)!;
    expect(user.scopedDocumentIds?.length).toBe(1);

    // What was rendered is what was stored.
    await expect(turn).toContainText(assistant.content.slice(0, 60).trim().split("\n")[0]);
  });

  test("TC-03.2 every citation's quoted text is really on the page it names", async ({
    request,
  }) => {
    const stored = await apiMessages(request, conversationId);
    const assistant = stored.filter((m) => m.role === "assistant").at(-1)!;
    const citations = assistant.citations ?? [];
    expect(citations.length).toBeGreaterThan(0);

    for (const c of citations) {
      expect(c.page, `citation ${c.n} names a page`).toBeGreaterThan(0);
      expect(c.citedText.trim().length).toBeGreaterThan(0);
      const pageText = (await apiPage(request, c.documentId, c.page)).text;
      expect(
        containsSpan(pageText, c.citedText),
        `citation ${c.n} quotes text that is not on ${c.documentTitle} page ${c.page}`,
      ).toBeTruthy();
    }
  });

  test("TC-03.3 the conversation was auto-titled from the first question", async ({
    request,
  }) => {
    const conv = (await apiConversations(request)).find((c) => c.id === conversationId)!;
    expect(
      conv.title,
      "the placeholder title should have been replaced by the first turn",
    ).not.toBe("New conversation");
    expect(conv.title.toLowerCase()).toContain("helmet");
  });

  test("TC-03.4 the answer body carries an inline citation chip per claim", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(answer.citations?.length ?? 0).toBeGreaterThan(0);

    // The chip is the product's trust primitive: the header badge counts sources, but
    // only a chip next to a claim says *which* source supports *that* claim. The
    // renderer builds chips from `{{cite:n}}` markers in the message text
    // (frontend/src/components/chat/Markdown.tsx), so an answer with citations and no
    // markers renders a source count nobody can follow.
    expect(
      answer.content,
      "the stored answer carries {{cite:n}} markers for its citations",
    ).toMatch(/\{\{cite:\d+\}\}/);

    await expect(
      ui.citationChips(page),
      "at least one citation chip is rendered beside a claim in the answer",
    ).not.toHaveCount(0);
  });

  test("TC-03.4b hovering a citation chip previews the passage", async ({ page }) => {
    await page.goto(`/chat/${conversationId}`);
    const chips = ui.citationChips(page);
    await expect(chips.first()).toBeVisible();

    await chips.first().hover();
    await expect(page.getByRole("button", { name: /^Open at page \d+$/ })).toBeVisible();
  });

  test("TC-03.5 clicking a citation chip opens the panel at the cited page, highlighted", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const stored = await apiMessages(request, conversationId);
    const citation = stored.filter((m) => m.role === "assistant").at(-1)!.citations![0];

    const chips = ui.citationChips(page);
    await expect(chips.first()).toBeVisible();

    await chips.first().click();

    const panel = ui.docPanel(page);
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("heading", { name: citation.documentTitle })).toBeVisible();
    // Opened at the cited page, not page 1.
    await expect(panel.getByRole("textbox", { name: /^Page number/ })).toHaveValue(
      String(citation.page),
    );
    // The footer names which source is being shown.
    await expect(panel.getByText(`Showing source ${citation.n}`)).toBeVisible();

    // The span is marked, and the viewer is not showing its "could not be found" notice.
    await expect(panel.locator("mark").first()).toBeVisible();
    await expect(
      page.getByText("The cited passage was not found in this page's text."),
    ).toHaveCount(0);

    // The URL now carries the panel, so the answer's source is a shareable link.
    await expect(page).toHaveURL(
      new RegExp(`doc=${citation.documentId}&page=${citation.page}`),
    );
  });

  test("TC-03.6 a pasted ?doc&page link opens the panel on load", async ({ page, request }) => {
    const stored = await apiMessages(request, conversationId);
    const citation = stored.filter((m) => m.role === "assistant").at(-1)!.citations![0];

    await page.goto(`/chat/${conversationId}?doc=${citation.documentId}&page=${citation.page}`);
    const panel = ui.docPanel(page);
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("textbox", { name: /^Page number/ })).toHaveValue(
      String(citation.page),
    );
  });

  test("TC-03.7 the panel pages through the document and Escape closes it", async ({ page }) => {
    await page.goto(`/chat/${conversationId}`);
    await page.getByRole("button", { name: /document panel$/ }).click();

    const panel = ui.docPanel(page);
    await expect(panel).toBeVisible();
    const pageField = panel.getByRole("textbox", { name: /^Page number/ });
    await expect(pageField).toHaveValue("1");
    await expect(panel.getByRole("button", { name: "Previous page" })).toBeDisabled();

    await panel.getByRole("button", { name: "Next page" }).click();
    await expect(pageField).toHaveValue("2");
    await expect(panel.getByText(/^Page 2 of \d+$/)).toBeVisible();
    await expect(page).toHaveURL(/page=2/);

    // Typing a page number navigates on Enter, not on each keystroke.
    await pageField.fill("3");
    await pageField.press("Enter");
    await expect(panel.getByText(/^Page 3 of \d+$/)).toBeVisible();

    await panel.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(page).not.toHaveURL(/doc=/);
  });

  test("TC-03.8 a follow-up keeps the scope and the transcript survives a reload", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    // The scope chip is restored from the last turn that carried one.
    await expect(
      page.getByRole("button", { name: `Remove ${EXISTING_DOC_TITLE} from scope` }),
    ).toBeVisible();

    await sendAndWait(page, "And what about the lighting requirements at night?");

    const stored = await apiMessages(request, conversationId);
    expect(stored.filter((m) => m.role === "user")).toHaveLength(2);
    const followUp = stored.filter((m) => m.role === "user").at(-1)!;
    expect(followUp.scopedDocumentIds?.length, "the follow-up inherited the scope").toBe(1);

    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;
    expect(answer.content.toLowerCase()).toMatch(/lamp|light|500 feet|reflector/);

    await page.reload();
    await expect(userBubble(page, "And what about the lighting requirements at night?")).toBeVisible();
    await expect(assistantTurns(page)).toHaveCount(2);
  });

  test("TC-03.9 a question the documents do not answer is abstained from, not invented", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    const id = await createConversation(page);
    await addToScope(page, EXISTING_DOC_TITLE);
    await sendAndWait(
      page,
      "What is the quarterly licence fee payable under this agreement, in dollars?",
    );

    const stored = await apiMessages(request, id);
    const answer = stored.filter((m) => m.role === "assistant").at(-1)!;

    // Either the UI's abstention notice, or an answer that plainly says the documents
    // do not address it. What must NOT happen is a confident fabricated figure.
    const abstained = answer.abstained === true;
    const saysNothingFound = /does not|do not|silent|no (?:provision|mention|reference)|not address|not contain/i.test(
      answer.content,
    );
    expect(
      abstained || saysNothingFound,
      `expected an abstention; got: ${answer.content.slice(0, 400)}`,
    ).toBeTruthy();
    expect(answer.content, "no invented dollar figure").not.toMatch(/\$\s?\d/);

    if (abstained) {
      await expect(page.getByText("Not found in the documents in scope.")).toBeVisible();
    }
  });
});
