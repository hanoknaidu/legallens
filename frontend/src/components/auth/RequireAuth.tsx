import { useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useSession } from "@/hooks/useSession";
import { setUnauthenticatedHandler } from "@/lib/api";

/**
 * The gate in front of every real route.
 *
 * Three states, and the middle one matters: while the session query is in flight we
 * know neither that the user is signed in nor that they are not. Redirecting then
 * would bounce a signed-in user to the login page on every reload, so it renders
 * nothing instead — a blank frame for one request rather than a visible flash of the
 * wrong screen.
 */
export function RequireAuth() {
  const { user, isLoading } = useSession();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    // A session can lapse mid-use: the app is open, the cookie expires, and the next
    // request 401s. `api` calls this, and this is where the app finds out.
    setUnauthenticatedHandler(() => {
      navigate("/login", { replace: true, state: { from: location.pathname + location.search } });
    });
    return () => setUnauthenticatedHandler(null);
  }, [navigate, location.pathname, location.search]);

  if (isLoading) return null;
  if (!user) {
    // `from` is what sends the user back where they were trying to go, rather than to
    // a generic landing page, once they sign in.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}
