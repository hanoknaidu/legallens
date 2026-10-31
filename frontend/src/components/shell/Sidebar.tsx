import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, NavLink } from "react-router-dom";
import { FileText, PenSquare, Settings } from "lucide-react";
import { api } from "@/lib/api";
import { DEFAULT_CONVERSATION_TITLE } from "@/lib/constants";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/stores/useUIStore";
import { Button } from "@/components/ui/Button";
import { Wordmark } from "./Wordmark";
import { MatterSwitcher } from "./MatterSwitcher";
import { ConversationList } from "@/components/conversations/ConversationList";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const activeMatterId = useUIStore((s) => s.activeMatterId);

  const create = useMutation({
    mutationFn: () => api.conversations.create({
        title: DEFAULT_CONVERSATION_TITLE,
        matterId: activeMatterId ?? "m-001",
      }),
    onSuccess: (conv) => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/chat/${conv.id}`);
      onNavigate?.();
    },
  });

  const link = ({ isActive }: { isActive: boolean }) =>
    cn(
      "flex items-center gap-2.5 rounded-md px-2.5 py-[7px] text-small transition-colors duration-[120ms]",
      isActive ? "bg-brand-50 text-brand-900" : "text-ink-muted hover:bg-sunken hover:text-ink",
    );

  return (
    <div className="flex h-full flex-col bg-canvas">
      <div className="px-3 pb-3 pt-3.5">
        <Wordmark />
      </div>

      <div className="px-2 pb-2">
        <MatterSwitcher />
      </div>

      <div className="px-2 pb-3">
        <Button
          variant="primary"
          className="w-full justify-start"
          onClick={() => create.mutate()}
          disabled={create.isPending}
        >
          <PenSquare size={15} />
          New chat
          <kbd className="ml-auto rounded-xs bg-white/15 px-1.5 py-0.5 font-mono text-micro">
            ⌘N
          </kbd>
        </Button>
      </div>

      <ConversationList />

      <div className="border-t border-line px-2 py-2">
        <NavLink to="/documents" className={link} onClick={onNavigate}>
          <FileText size={15} /> Documents
        </NavLink>
        <NavLink to="/settings" className={link} onClick={onNavigate}>
          <Settings size={15} /> Settings
        </NavLink>
      </div>
    </div>
  );
}
