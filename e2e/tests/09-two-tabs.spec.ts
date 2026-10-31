import { expect, test } from "@playwright/test";
import {
  API,
  EXISTING_DOC_TITLE,
  addToScope,
  apiMessages,
  createConversation,
  sendAndWait,
  ui,
} from "../helpers/app";

/**
 * TC-09 — two tabs on the same app.
 *
 * Not a contrived case: one user with the library open in one tab and a thread in
 * another is ordinary. It is also the smallest case that exercises the two pieces of
 * in-process state the service keeps — the pending-turn registry and the per-owner
 * stop — so it is where "single-user" stops being free.
 */
test.describe.serial("TC-09 two tabs", () => {
  test("TC-09.1 a conversation made in one tab reaches the other on reload", async ({
    context,
  }) => {
    const a = await context.newPage();
    const b = await context.newPage();
    await a.goto("/");
    await b.goto("/");

    const id = await createConversation(a);

    // Deliberately asserted on reload rather than on tab focus. The query client sets
    // `refetchOnWindowFocus: false` with a 30s `staleTime` (app/providers.tsx), so a
    // background tab is *designed* not to update when you switch to it. Asserting
    // otherwise would report a configuration choice as a bug; what the app does
    // promise is that a reload shows the truth.
    await b.bringToFront();
    await b.reload();
    await expect(ui.conversationRow(b, id)).toBeVisible({ timeout: 30_000 });

    await a.close();
    await b.close();
  });

  test("TC-09.2 Stop in one tab must not cancel the other tab's answer", async ({
    context,
    request,
  }) => {
    // `PendingTurns.cancel()` is keyed on the owner, and the stop button posts no
    // message id, so the no-id branch runs and signals every turn this owner has in
    // flight. With one tab that is the turn you meant; with two it is also the one you
    // did not. Documented in the registry as a Phase-1 simplification — this test
    // pins what it actually costs, so the cost is a decision rather than a surprise.
    // DEFECT (confirmed): `cancel()` takes the owner and, with no message id, signals
    // every turn that owner has in flight. The stop button posts no body, so that is
    // the branch that runs. Backend log for this test: `stop requested {cancelled: 8}`.
    test.fail();
    const a = await context.newPage();
    const b = await context.newPage();
    await a.goto("/");
    await b.goto("/");

    const convA = await createConversation(a);
    const convB = await createConversation(b);

    // Tab B starts a long answer.
    await b.bringToFront();
    await addToScope(b, EXISTING_DOC_TITLE);
    await ui.composer(b).fill(
      "Explain in detail every requirement this statute places on a bicycle rider.",
    );
    await ui.sendButton(b).click();
    await expect(ui.stopButton(b)).toBeVisible({ timeout: 60_000 });

    // Tab A starts one too, then stops its own.
    await a.bringToFront();
    await addToScope(a, EXISTING_DOC_TITLE);
    await ui.composer(a).fill("List every lighting requirement in this statute.");
    await ui.sendButton(a).click();
    await expect(ui.stopButton(a)).toBeVisible({ timeout: 60_000 });
    await ui.stopButton(a).click();

    // Tab B's answer should still land.
    await b.bringToFront();
    await expect(ui.stopButton(b)).toBeHidden({ timeout: 240_000 });

    const storedB = await apiMessages(request, convB);
    const answerB = storedB.filter((m) => m.role === "assistant").at(-1);
    expect(
      answerB,
      "the untouched tab's turn was persisted — stopping one tab did not cancel it",
    ).toBeTruthy();
    expect(
      answerB!.content.length + (answerB!.draft ? 1 : 0),
      "tab B's answer is empty: it was cancelled by tab A's stop and persisted blank, " +
        "with no marker on the message saying it was stopped",
    ).toBeGreaterThan(0);

    await request.delete(`${API}/api/conversations/${convA}`);
    await request.delete(`${API}/api/conversations/${convB}`);
    await a.close();
    await b.close();
  });

  test("TC-09.3 a conversation deleted in one tab disappears from the other", async ({
    context,
    request,
  }) => {
    const a = await context.newPage();
    const b = await context.newPage();
    await a.goto("/");
    const id = await createConversation(a);
    await b.goto("/");
    await expect(ui.conversationRow(b, id)).toBeVisible();

    expect((await request.delete(`${API}/api/conversations/${id}`)).status()).toBe(204);

    await b.bringToFront();
    await b.reload();
    await expect(ui.conversationRow(b, id)).toHaveCount(0);

    // And the tab that still has it open gets a 404 rather than a blank thread, with
    // nothing invented to fill the gap.
    await a.bringToFront();
    await a.reload();
    await expect(a.locator("main")).not.toContainText("undefined");

    await a.close();
    await b.close();
  });

  test("TC-09.4 a turn cancelled from elsewhere says so instead of rendering blank", async ({
    context,
    request,
  }) => {
    // The two-tab cancel is the one deterministic way to produce a turn that was
    // persisted having generated nothing. A same-tab Stop aborts the SSE connection,
    // which cancels the server's generator before it persists anything at all — so
    // there is no empty turn to look at, only a missing one.
    //
    // What is under test here is the *rendering*, not the cancel: before this, such a
    // turn drew as a blank bubble under a "Summary · N sources" header, with no answer
    // and nothing to say it had failed.
    const a = await context.newPage();
    const b = await context.newPage();
    await a.goto("/");
    await b.goto("/");

    const convA = await createConversation(a);
    const convB = await createConversation(b);

    await b.bringToFront();
    await addToScope(b, EXISTING_DOC_TITLE);
    await ui.composer(b).fill(`Summarise ${EXISTING_DOC_TITLE}.`);
    await ui.sendButton(b).click();
    await expect(ui.stopButton(b)).toBeVisible({ timeout: 60_000 });

    await a.bringToFront();
    await addToScope(a, EXISTING_DOC_TITLE);
    await ui.composer(a).fill("List every lighting requirement in this statute.");
    await ui.sendButton(a).click();
    await expect(ui.stopButton(a)).toBeVisible({ timeout: 60_000 });
    await ui.stopButton(a).click();

    await b.bringToFront();
    await expect(ui.stopButton(b)).toBeHidden({ timeout: 240_000 });

    const answer = (await apiMessages(request, convB)).filter((m) => m.role === "assistant").at(-1);
    test.skip(
      !answer || Boolean(answer.summary) || (answer.content ?? "").trim().length > 0,
      "tab B's summary completed before the cancel landed, so there is no empty turn",
    );

    await b.reload();
    await expect(b.getByText("This answer did not complete.")).toBeVisible();
    // And not dressed up as an abstention: "the documents are silent" is a claim
    // about the documents that nothing here established.
    await expect(b.getByText("Not found in the documents in scope.")).toHaveCount(0);
    await expect(b.getByText(/\d+ sources?$/)).toHaveCount(0);
    await expect(b.getByRole("button", { name: "Ask again" })).toBeVisible();

    await request.delete(`${API}/api/conversations/${convA}`);
    await request.delete(`${API}/api/conversations/${convB}`);
    await a.close();
    await b.close();
  });
});
