import { useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, Eye, EyeOff, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ApiError } from "@/lib/api";
import { useSession, useSignIn } from "@/hooks/useSession";

/**
 * Sign-in.
 *
 * The decisions here that are not cosmetic:
 *
 * * **One error line, never two.** The server deliberately cannot distinguish a wrong
 *   password from an unknown address, so neither can this page. Inventing a
 *   per-field message ("no account with that email") would leak through the UI exactly
 *   what the API refuses to leak.
 * * **The browser is allowed to help.** Real `name` and `autocomplete` attributes mean
 *   password managers work. A login form that defeats them pushes people towards
 *   passwords they can retype from memory, which are worse.
 * * **The error is announced.** `role="alert"` so a screen reader hears the failure;
 *   without it the only feedback is a visual change the user may not be looking at.
 * * **Submission is a form submit**, not a click handler on a button, so Enter works
 *   from either field without extra key handling.
 */
export function LoginPage() {
  const { user, openSignin, isLoading: sessionLoading } = useSession();
  const signIn = useSignIn();
  const navigate = useNavigate();
  const location = useLocation();
  const emailRef = useRef<HTMLInputElement>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [revealed, setRevealed] = useState(false);

  // Where the guard was trying to send them before it found no session.
  const from = (location.state as { from?: string } | null)?.from ?? "/chat/new";

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  // Already signed in — arriving here from a bookmark, or in a second tab. Nothing to
  // ask, so nothing is shown.
  if (!sessionLoading && user) return <Navigate to={from} replace />;

  const failure = signIn.error;
  const message =
    failure instanceof ApiError
      ? (failure.detail ?? failure.message)
      : failure
        ? "Could not reach the server. Check your connection and try again."
        : null;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password || signIn.isPending) return;
    signIn.mutate(
      { email: email.trim(), password },
      {
        onSuccess: () => navigate(from, { replace: true }),
        onError: () => {
          // Clear the password, keep the address. Retyping an email you already typed
          // correctly is friction; leaving a wrong password in the field invites the
          // same failure again.
          setPassword("");
        },
      },
    );
  };

  return (
    <main className="flex min-h-dvh items-center justify-center bg-sunken px-4 py-10">
      <div className="w-full max-w-sm">
        <header className="mb-7 text-center">
          <h1 className="text-title font-semibold tracking-tight text-ink">Legal Lens</h1>
          <p className="mt-1.5 text-ui text-ink-muted">Sign in to your document library.</p>
        </header>

        <form
          onSubmit={submit}
          noValidate
          className="rounded-lg border border-line bg-surface p-6 shadow-sm"
        >
          {message && (
            <div
              role="alert"
              className="mb-5 flex gap-2 rounded-md border border-risk-br bg-risk-bg
                         px-3 py-2.5 text-caption text-risk-fg"
            >
              <AlertCircle className="mt-px size-4 shrink-0" aria-hidden />
              <span>{message}</span>
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label htmlFor="email" className="mb-1.5 block text-caption font-medium text-ink">
                Email
              </label>
              <Input
                ref={emailRef}
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                spellCheck={false}
                autoCapitalize="none"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={signIn.isPending}
                aria-invalid={message ? true : undefined}
                placeholder="you@firm.com"
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-1.5 block text-caption font-medium text-ink">
                Password
              </label>
              <div className="relative">
                <Input
                  id="password"
                  name="password"
                  type={revealed ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={signIn.isPending}
                  aria-invalid={message ? true : undefined}
                  className="pr-10"
                />
                {/* Typing a long passphrase blind is how people end up choosing short
                    ones. Not in the tab order: it is a convenience, and a keyboard
                    user tabbing from password should reach the submit button. */}
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => setRevealed((v) => !v)}
                  aria-label={revealed ? "Hide password" : "Show password"}
                  className="absolute inset-y-0 right-0 grid w-10 place-items-center
                             text-ink-subtle transition-colors hover:text-ink"
                >
                  {revealed ? (
                    <EyeOff className="size-4" aria-hidden />
                  ) : (
                    <Eye className="size-4" aria-hidden />
                  )}
                </button>
              </div>
            </div>
          </div>

          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="mt-6 w-full"
            disabled={signIn.isPending || !email.trim() || !password}
          >
            {signIn.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Signing in…
              </>
            ) : (
              "Sign in"
            )}
          </Button>
        </form>

        {/* There is no separate sign-up link in either mode: with open sign-in this
            form *is* the sign-up, and without it there is nothing to link to. Which
            of the two is true comes from the server (docs/auth.md) rather than from a
            build-time constant, so this cannot tell half of all deployments the wrong
            thing. */}
        <p className="mt-5 text-center text-caption text-ink-subtle">
          {openSignin
            ? "New here? Signing in creates your account."
            : "Accounts are created by your administrator."}
        </p>
      </div>
    </main>
  );
}
