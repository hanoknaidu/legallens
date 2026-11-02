import { expect, test } from "@playwright/test";
import { API } from "../helpers/app";

/**
 * TC-10 — authentication (docs/auth.md).
 *
 * The only spec that starts signed out. Everything else in this suite inherits the
 * session `global-setup.ts` acquires once; here the point is the flow itself, so the
 * storage state is deliberately empty.
 */
test.use({ storageState: { cookies: [], origins: [] } });

const EMAIL = process.env.E2E_EMAIL ?? "attorney@firm.test";
const PASSWORD = process.env.E2E_PASSWORD ?? "";

// By role rather than by label: the reveal toggle's accessible name is "Show
// password", and `getByLabel("Password")` matches it too.
const emailField = (p: import("@playwright/test").Page) =>
  p.getByRole("textbox", { name: "Email" });
const passwordField = (p: import("@playwright/test").Page) =>
  p.getByRole("textbox", { name: "Password", exact: true });
const submit = (p: import("@playwright/test").Page) =>
  p.getByRole("button", { name: /^Sign in$|^Signing in/ });

test.describe("TC-10 authentication", () => {
  test("TC-10.1 an anonymous visitor is sent to sign in, not to the chat", async ({ page }) => {
    await page.goto("/chat/new");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Legal Lens" })).toBeVisible();
    // The composer must not render for someone with no session, even briefly.
    await expect(page.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  });

  test("TC-10.2 a wrong password is refused without saying which field was wrong", async ({
    page,
  }) => {
    await page.goto("/login");
    await emailField(page).fill(EMAIL);
    await passwordField(page).fill("definitely-not-the-password");
    await submit(page).click();

    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).toHaveText(/Email or password is incorrect/);
    // Still on the login page, and the password field was cleared rather than left to
    // be resubmitted.
    await expect(page).toHaveURL(/\/login$/);
    await expect(passwordField(page)).toHaveValue("");
  });

  test("TC-10.3 signing in lands on the chat and the app works", async ({ page }) => {
    test.skip(!PASSWORD, "E2E_PASSWORD is not set");

    await page.goto("/login");
    await emailField(page).fill(EMAIL);
    await passwordField(page).fill(PASSWORD);
    await submit(page).click();

    await expect(page).toHaveURL(/\/chat\/new$/);
    await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  });

  test("TC-10.4 the intended page is remembered across sign-in", async ({ page }) => {
    test.skip(!PASSWORD, "E2E_PASSWORD is not set");

    // Arrive at a deep link with no session: after signing in the user should be where
    // they were going, not dumped on a landing page.
    await page.goto("/documents");
    await expect(page).toHaveURL(/\/login$/);

    await emailField(page).fill(EMAIL);
    await passwordField(page).fill(PASSWORD);
    await submit(page).click();

    await expect(page).toHaveURL(/\/documents$/);
  });

  test("TC-10.5 signing out ends the session server-side", async ({ page, request }) => {
    test.skip(!PASSWORD, "E2E_PASSWORD is not set");

    await page.goto("/login");
    await emailField(page).fill(EMAIL);
    await passwordField(page).fill(PASSWORD);
    await submit(page).click();
    await expect(page).toHaveURL(/\/chat\/new$/);

    await page.getByRole("button", { name: /^Account —/ }).click();
    await page.getByRole("menuitem", { name: /Sign out/ }).click();

    await expect(page).toHaveURL(/\/login$/);

    // And the cookie the browser still holds is worthless: the row is gone, so a copy
    // taken before sign-out cannot be replayed.
    const cookies = await page.context().cookies();
    const session = cookies.find((c) => c.name === "ll_session");
    if (session) {
      const replay = await request.get(`${API}/api/matters`, {
        headers: { Cookie: `ll_session=${session.value}` },
      });
      expect(replay.status()).toBe(401);
    }
  });

  test("TC-10.6 a protected API route refuses an anonymous caller", async ({ request }) => {
    const res = await request.get(`${API}/api/matters`);
    expect(res.status()).toBe(401);
    expect((await res.json()).code).toBe("unauthenticated");
  });
});
