import { LogOut } from "lucide-react";
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
  const { user } = useSession();
  const signOut = useSignOut();

  if (!user) return null;

  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Account — signed in as ${user.email}`}>
          <span
            className="grid size-6 place-items-center rounded-full bg-brand-50
                       text-micro font-semibold text-brand-700"
            aria-hidden
          >
            {initials(user.name, user.email)}
          </span>
        </Button>
      </MenuTrigger>
      <MenuContent>
        <div className="px-2 py-1.5">
          <p className="truncate text-caption font-medium text-ink">{user.name}</p>
          <p className="truncate text-micro text-ink-subtle">{user.email}</p>
        </div>
        <MenuSeparator />
        <MenuItem onSelect={() => signOut.mutate()}>
          <LogOut className="size-3.5" aria-hidden />
          {signOut.isPending ? "Signing out…" : "Sign out"}
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}
