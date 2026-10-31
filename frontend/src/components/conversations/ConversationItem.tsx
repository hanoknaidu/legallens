import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { NavLink } from "react-router-dom";
import { cn } from "@/lib/cn";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/Menu";
import type { Conversation } from "@/types/domain";

export function ConversationItem({
  conversation,
  onRename,
  onDelete,
}: {
  conversation: Conversation;
  onRename: (c: Conversation) => void;
  onDelete: (c: Conversation) => void;
}) {
  return (
    <NavLink
      to={`/chat/${conversation.id}`}
      className={({ isActive }) =>
        cn(
          "group relative flex items-center gap-1 rounded-md pl-2.5 pr-1 py-[7px]",
          "transition-colors duration-[120ms]",
          isActive ? "bg-brand-50 text-brand-900" : "text-ink-muted hover:bg-sunken hover:text-ink",
        )
      }
    >
      {({ isActive }) => (
        <>
          <span
            className={cn(
              "absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-brand-600",
              isActive ? "opacity-100" : "opacity-0",
            )}
          />
          <span className="min-w-0 flex-1 truncate text-small">{conversation.title}</span>
          <Menu>
            <MenuTrigger asChild>
              <button
                onClick={(e) => e.preventDefault()}
                aria-label={`Actions for ${conversation.title}`}
                className="shrink-0 rounded-sm p-1 text-ink-subtle opacity-0 transition-opacity
                           hover:bg-line/70 hover:text-ink focus-visible:opacity-100
                           group-hover:opacity-100 data-[state=open]:opacity-100"
              >
                <MoreHorizontal size={14} />
              </button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem onSelect={() => onRename(conversation)}>
                <Pencil size={14} /> Rename
              </MenuItem>
              <MenuItem tone="danger" onSelect={() => onDelete(conversation)}>
                <Trash2 size={14} /> Delete
              </MenuItem>
            </MenuContent>
          </Menu>
        </>
      )}
    </NavLink>
  );
}
