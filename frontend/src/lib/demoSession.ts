/**
 * The showcase build's stand-in for a session.
 *
 * When the server reports `authRequired: false` it enforces nothing, so there is no
 * real session to hold. Without *something*, though, the login page could never be
 * shown twice: the guard would wave everyone through and the demo would open straight
 * into the chat, which is the one thing a showcase of the sign-in flow must not do.
 *
 * So the flow is gated on this flag instead. It is not a credential and protects
 * nothing — it exists so that "signed in" is a state the demo can enter and leave.
 *
 * `sessionStorage`, not `localStorage`: a demo should start at the login page in each
 * new tab, and be forgotten when the tab closes.
 */
const KEY = "legal-lens.demo-signed-in";

export function isDemoSignedIn(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "true";
  } catch {
    // Private browsing and some embedded webviews throw on storage access. Treating
    // that as "not signed in" would trap the visitor on the login page forever, so
    // the demo opens instead — it is guarding nothing.
    return true;
  }
}

export function setDemoSignedIn(value: boolean): void {
  try {
    if (value) sessionStorage.setItem(KEY, "true");
    else sessionStorage.removeItem(KEY);
  } catch {
    // Same reasoning: nothing here is load-bearing.
  }
}
