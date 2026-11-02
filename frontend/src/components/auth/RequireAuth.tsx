import { useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useSession } from "@/hooks/useSession";
import { isDemoSignedIn } from "@/lib/demoSession";
import { setUnauthenticatedHandler } from "@/lib/api";

/**
 * The gate in front of every real route.
 *
 * Two builds, one component:
 *
 * * **Real** (`authRequired`) — a session decides, and a 401 from any request sends
 *   the visitor back to sign in.
 * * **Showcase** — the server enforces nothing, so the gate falls back to the demo
 *   flag (`lib/demoSession`). It guards nothing; it is what lets the login page be
 *   shown at all, since otherwise the app would open straight into the chat and the
 *   flow being demonstrated would never appear.
 *
 * Three states in both, and the middle one matters: while the session query is in
 * flight we know neither that the visitor may enter nor that they may not.
 * Redirecting then would bounce a signed-in user to the login page on every reload,
 * so it renders nothing — a blank frame for one request rather than a visible flash
 * of the wrong screen.
 */
export function RequireAuth() {
  const { user, authRequired, isLoading } = useSession();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    // Only the real build can produce a 401 mid-session. Registering this in the
    // showcase build would be harmless but misleading — there is nothing to expire.
    if (!authRequired) return;
    setUnauthenticatedHandler(() => {
      navigate("/login", { replace: true, state: { from: location.pathname + location.search } });
    });
    return () => setUnauthenticatedHandler(null);
  }, [authRequired, navigate, location.pathname, location.search]);

  if (isLoading) return null;

  const admitted = authRequired ? user !== null : isDemoSignedIn();
  if (!admitted) {
    // `from` is what sends the visitor back where they were trying to go, rather than
    // to a generic landing page, once they are through.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}
