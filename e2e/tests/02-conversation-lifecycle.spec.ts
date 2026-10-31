import { expect, test } from "@playwright/test";
import {
  API,
  apiConversations,
  createConversation,
  deleteFromSidebar,
  renameFromSidebar,
  ui,
} from "../helpers/app";

/**
 * TC-02 — create, rename, delete. Scenarios 1, 2 and 3 of the brief.
 *
 * Every assertion is made twice: once against the UI, once against the API, because
 * the sidebar's mutations are optimistic. An optimistic rename the server rejected
 * looks exactly like one that worked until the next refetch, so a test that only read
 * the DOM would pass against a backend that persisted nothing.
 */
test.describe.serial("TC-02 conversation lifecycle", () => {
  const RENAMED = "E2E · Renamed from the sidebar";
  const RENAMED_TOPBAR = "E2E · Renamed from the top bar";
  let conversationId: string;

  test("TC-02.1 a new conversation appears in the sidebar and is persisted", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    const before = await apiConversations(request);

    conversationId = await createConversation(page);

    // The thread is open, titled with the placeholder, and empty.
    await expect(ui.topBarTitle(page)).toHaveText("New conversation");
    await expect(page.getByRole("heading", { level: 2 })).toContainText(
      "Ask, summarise or draft",
    );

    const after = await apiConversations(request);
    expect(after.length).toBe(before.length + 1);
    const created = after.find((c) => c.id === conversationId);
    expect(created, "the created conversation is in GET /conversations").toBeTruthy();
    expect(created!.title).toBe("New conversation");

    // And it is the selected row in the sidebar, under today's group.
    await expect(ui.conversationNav(page).getByRole("heading", { name: "Today" })).toBeVisible();
    const row = ui.conversationRow(page, conversationId);
    await expect(row).toBeVisible();
    await expect(row).toHaveAttribute("aria-current", "page");
  });

  test("TC-02.2 ⌘N creates another one", async ({ page, request }) => {
    await page.goto("/");
    const before = await apiConversations(request);
    await page.keyboard.press("ControlOrMeta+n");
    await page.waitForURL(/\/chat\/c-[0-9a-f]+/);
    const id = page.url().match(/\/chat\/(c-[0-9a-f]+)/)![1];
    await expect(ui.conversationRow(page, id)).toBeVisible();

    const after = await apiConversations(request);
    expect(after.length).toBe(before.length + 1);

    // Cleaned up through the API: this test is about the shortcut, not the delete path.
    expect((await request.delete(`${API}/api/conversations/${id}`)).status()).toBe(204);
  });

  test("TC-02.3 renaming from the sidebar menu persists", async ({ page, request }) => {
    await page.goto(`/chat/${conversationId}`);
    await renameFromSidebar(page, conversationId, RENAMED);

    // Optimistic, so the row changes at once...
    await expect(ui.conversationRow(page, conversationId)).toContainText(RENAMED);
    // ...and the open thread's title follows, because both read one cache entry.
    await expect(ui.topBarTitle(page)).toHaveText(RENAMED);

    const stored = (await apiConversations(request)).find((c) => c.id === conversationId);
    expect(stored!.title, "the rename reached the database").toBe(RENAMED);

    // A reload is the real test of persistence: it discards every optimistic update.
    await page.reload();
    await expect(ui.topBarTitle(page)).toHaveText(RENAMED);
  });

  test("TC-02.4 renaming in place from the top bar persists", async ({ page, request }) => {
    await page.goto(`/chat/${conversationId}`);
    await ui.topBarTitle(page).click();
    const field = page.getByRole("textbox", { name: "Conversation title" });
    await field.fill(RENAMED_TOPBAR);
    await field.press("Enter");

    await expect(ui.topBarTitle(page)).toHaveText(RENAMED_TOPBAR);
    await expect(ui.conversationRow(page, conversationId)).toContainText(RENAMED_TOPBAR);

    const stored = (await apiConversations(request)).find((c) => c.id === conversationId);
    expect(stored!.title).toBe(RENAMED_TOPBAR);
  });

  test("TC-02.5 Escape abandons an in-place rename, and a blank title is refused", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);

    await ui.topBarTitle(page).click();
    let field = page.getByRole("textbox", { name: "Conversation title" });
    await field.fill("discarded");
    await field.press("Escape");
    await expect(ui.topBarTitle(page)).toHaveText(RENAMED_TOPBAR);

    await ui.topBarTitle(page).click();
    field = page.getByRole("textbox", { name: "Conversation title" });
    await field.fill("   ");
    await field.press("Enter");
    await expect(ui.topBarTitle(page)).toHaveText(RENAMED_TOPBAR);

    const stored = (await apiConversations(request)).find((c) => c.id === conversationId);
    expect(stored!.title, "neither edit was saved").toBe(RENAMED_TOPBAR);
  });

  test("TC-02.6 the sidebar search filters by title", async ({ page }) => {
    await page.goto(`/chat/${conversationId}`);
    await ui.searchConversations(page).fill("Renamed from the top");
    await expect(ui.conversationRow(page, conversationId)).toBeVisible();
    await expect(ui.conversationNav(page).getByRole("link")).toHaveCount(1);

    await ui.searchConversations(page).fill("zzz-no-such-conversation");
    await expect(page.getByText(/No conversations match/)).toBeVisible();
    await ui.searchConversations(page).fill("");
    await expect(ui.conversationRow(page, conversationId)).toBeVisible();
  });

  test("TC-02.7 cancelling the delete dialog keeps the conversation", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    const row = ui.conversationRow(page, conversationId);
    await row.hover();
    await row.getByRole("button", { name: /^Actions for / }).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();

    // The dialog names the conversation and says the delete cannot be undone.
    await expect(page.getByText("Delete conversation?")).toBeVisible();
    await expect(page.getByText(/cannot be undone/)).toBeVisible();
    await expect(page.getByText(RENAMED_TOPBAR, { exact: false }).first()).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();

    await expect(ui.conversationRow(page, conversationId)).toBeVisible();
    const stored = (await apiConversations(request)).find((c) => c.id === conversationId);
    expect(stored, "cancel did not delete it").toBeTruthy();
  });

  test("TC-02.8 deleting removes the row, redirects away, and purges it server-side", async ({
    page,
    request,
  }) => {
    await page.goto(`/chat/${conversationId}`);
    await deleteFromSidebar(page, conversationId);

    // The open thread was the deleted one, so the app leaves it.
    await page.waitForURL(/\/chat\/new$/);
    await expect(ui.conversationRow(page, conversationId)).toHaveCount(0);

    const stored = (await apiConversations(request)).find((c) => c.id === conversationId);
    expect(stored, "it is gone from GET /conversations").toBeFalsy();

    // Its messages are a 404, not an empty list: the conversation does not exist.
    const res = await request.get(`${API}/api/conversations/${conversationId}/messages`);
    expect(res.status()).toBe(404);

    // Surviving a reload rules out an optimistic cache hiding a failed delete.
    await page.reload();
    await expect(ui.conversationRow(page, conversationId)).toHaveCount(0);
  });

  test("TC-02.9 deleting a thread the user is not looking at leaves them where they are", async ({
    page,
    request,
  }) => {
    // DEFECT (confirmed): the user is sent to /chat/new whatever they were looking at.
    // A consequence of TC-02.10 below — the row menu navigates into the conversation
    // first, so by the time the delete runs, `id === conversationId` is true.
    test.fail();
    await page.goto("/");
    const victim = await createConversation(page);
    await renameFromSidebar(page, victim, "E2E · Victim");

    const keeper = await createConversation(page);
    await renameFromSidebar(page, keeper, "E2E · Keeper");
    await expect(page).toHaveURL(new RegExp(keeper));

    await deleteFromSidebar(page, victim);
    await expect(page).toHaveURL(new RegExp(keeper));
    await expect(ui.conversationRow(page, victim)).toHaveCount(0);
    await expect(ui.conversationRow(page, keeper)).toBeVisible();

    const ids = (await apiConversations(request)).map((c) => c.id);
    expect(ids).not.toContain(victim);
    expect(ids).toContain(keeper);

    await request.delete(`${API}/api/conversations/${keeper}`);
  });

  test("TC-02.10 opening a row's menu must not navigate into that conversation", async ({
    page,
    request,
  }) => {
    // A regression guard for the click-through below. The menu lives inside the row's
    // NavLink, and Radix renders its content through a React portal — which bubbles
    // events up the *React* tree, not the DOM tree. So selecting a menu item fires the
    // link the trigger is nested inside.
    // DEFECT (confirmed): selecting Rename or Delete navigates into the conversation.
    test.fail();
    const created = await request.post(`${API}/api/conversations`, {
      data: { title: "E2E · Menu click-through", matterId: "m-001" },
    });
    const id = (await created.json()).id as string;

    await page.goto("/documents");
    const row = ui.conversationRow(page, id);
    await row.hover();
    await row.getByRole("button", { name: /^Actions for / }).click();

    // Opening the menu is fine.
    await expect(page.getByRole("menuitem", { name: "Rename" })).toBeVisible();
    await expect(page, "merely opening the menu should not navigate").toHaveURL(/\/documents$/);

    // Selecting an item should open the dialog and leave the route alone.
    await page.getByRole("menuitem", { name: "Rename" }).click();
    await expect(page.getByRole("textbox", { name: "Conversation title" })).toBeVisible();
    await expect(
      page,
      "choosing Rename must not take the user away from the page they were on",
    ).toHaveURL(/\/documents$/);

    await request.delete(`${API}/api/conversations/${id}`);
  });
});
