import { LogOut } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/Menu";
import { useSession, useSignOut } from "@/hooks/useSession";

/** Initials for the avatar: "Test Attorney" → TA, "attorney@firm.com" → A. */
function initials(name: string, email: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[words.length - 1][0]).toUpperCase();
  if (words.length === 1) return words[0][0].toUpperCase();
  return (email[0] ?? "?").toUpperCase();
}

/**
 * Who is signed in, and the way out.
 *
 * Signing out is a menu item rather than a confirm dialog: it is reversible in one
 * step — sign in again — and a dialog for a reversible action is friction for nothing.
 * The email is shown because a shared machine is the normal case in a firm, and
 * "whose library am I looking at?" should not require a click.
 */
export function UserMenu() {
  const { user, authRequired } = useSession();
  const signOut = useSignOut();
  const navigate = useNavigate();

  // The showcase build has no signed-in user to describe, but it still needs the way
  // out — otherwise the demo can be entered once and never replayed.
  const identity = user ?? { name: "Demo", email: "Demonstration build" };

  if (authRequired && !user) return null;

  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Account — signed in as ${identity.email}`}>
          <span
            className="grid size-6 place-items-center rounded-full bg-brand-50
                       text-micro font-semibold text-brand-700"
            aria-hidden
          >
            {initials(identity.name, identity.email)}
          </span>
        </Button>
      </MenuTrigger>
      <MenuContent>
        <div className="px-2 py-1.5">
          <p className="truncate text-caption font-medium text-ink">{identity.name}</p>
          <p className="truncate text-micro text-ink-subtle">{identity.email}</p>
        </div>
        <MenuSeparator />
        <MenuItem
          onSelect={() =>
            signOut.mutate(undefined, {
              // The real build is sent back by the guard, which reacts to the session
              // going null. Nothing changes in the showcase build, so it navigates
              // explicitly — `onSettled`, because a failed sign-out should still
              // leave rather than strand someone in the app.
              onSettled: () => navigate("/login", { replace: true }),
            })
          }
        >
          <LogOut className="size-3.5" aria-hidden />
          {signOut.isPending ? "Signing out…" : "Sign out"}
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
