import { http, HttpResponse } from "msw";
import type { SessionUser } from "@/types/domain";

/**
 * The mock layer's session.
 *
 * `VITE_USE_MOCKS=true` is the only path with demo content, and it has no backend to
 * hold a cookie — so the session lives in a module variable here. It starts *signed
 * out*, so the mock path exercises the login screen rather than skipping it: a sign-in
 * form nobody ever sees in development is a sign-in form that breaks without anyone
 * noticing.
 *
 * Any password of 12 characters or more is accepted. This is a fixture, and the real
 * rule lives in the backend; rejecting passwords here would only teach the developer
 * a rule that is not the real one.
 */
const DEMO_USER: SessionUser = {
  id: "owner-local",
  email: "attorney@firm.test",
  name: "Dana Whitfield",
  expiresAt: new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString(),
};

let signedIn = false;

function problem(status: number, title: string, detail: string, code: string) {
  return HttpResponse.json(
    { type: "about:blank", title, status, detail, code, requestId: "mock" },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

export const authHandlers = [
  http.get("/api/auth/session", () =>
    HttpResponse.json({ user: signedIn ? DEMO_USER : null }),
  ),

  http.post("/api/auth/signin", async ({ request }) => {
    const { email, password } = (await request.json()) as { email?: string; password?: string };
    if (!email || !password || password.length < 12) {
      // The same single message the real backend gives, for the same reason: the UI
      // must not be able to tell a wrong password from an unknown address.
      return problem(401, "Sign-in failed", "Email or password is incorrect.", "invalid_credentials");
    }
    signedIn = true;
    return HttpResponse.json({ user: { ...DEMO_USER, email } });
  }),

  http.post("/api/auth/signout", () => {
    signedIn = false;
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/auth/me", () =>
    signedIn
      ? HttpResponse.json(DEMO_USER)
      : problem(401, "Not signed in", "This request needs a signed-in session.", "unauthenticated"),
  ),
];
