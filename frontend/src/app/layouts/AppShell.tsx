import { useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import { DEFAULT_CONVERSATION_TITLE } from "@/lib/constants";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/stores/useUIStore";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { Sidebar } from "@/components/shell/Sidebar";
import { CommandPalette } from "@/components/shell/CommandPalette";
import { DocumentPanel } from "@/components/documents/DocumentPanel";
import { BackendStatus } from "@/components/common/BackendStatus";
import { Button } from "@/components/ui/Button";

export function AppShell() {
  const { sidebarOpen, setSidebarOpen, toggleSidebar, activeMatterId } = useUIStore();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const isCompact = useMediaQuery("(max-width: 1023px)");
  const navigate = useNavigate();
  const qc = useQueryClient();

  const create = useMutation({
    mutationFn: () => api.conversations.create({
        title: DEFAULT_CONVERSATION_TITLE,
        matterId: activeMatterId ?? "m-001",
      }),
    onSuccess: (conv) => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/chat/${conv.id}`);
    },
  });

  // On compact viewports the sidebar is an overlay, so it starts closed.
  useEffect(() => {
    if (isCompact) setSidebarOpen(false);
  }, [isCompact, setSidebarOpen]);

  useKeyboardShortcuts({
    "mod+b": toggleSidebar,
    "mod+n": () => create.mutate(),
    "mod+k": () => setPaletteOpen(true),
    escape: () => isCompact && setSidebarOpen(false),
  });

  return (
    <div className="flex h-dvh w-full flex-col overflow-hidden bg-canvas">
      <BackendStatus />

      <div className="flex min-h-0 w-full flex-1 overflow-hidden">
        {isCompact && sidebarOpen && (
          <div
            className="fixed inset-0 z-30 bg-ink/25 backdrop-blur-[1px]"
            onClick={() => setSidebarOpen(false)}
            aria-hidden="true"
          />
        )}

        <aside
          className={cn(
            "shrink-0 border-r border-line transition-[width,transform] duration-[180ms]",
            "[transition-timing-function:cubic-bezier(.2,.8,.2,1)]",
            isCompact
              ? cn(
                  "fixed inset-y-0 left-0 z-40 w-sidebar",
                  sidebarOpen ? "translate-x-0" : "-translate-x-full",
                )
              : cn("relative", sidebarOpen ? "w-sidebar" : "w-0 overflow-hidden"),
          )}
          aria-hidden={!sidebarOpen}
        >
          {isCompact && sidebarOpen && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close sidebar"
              className="absolute right-2 top-3.5 z-10"
              onClick={() => setSidebarOpen(false)}
            >
              <X size={15} />
            </Button>
          )}
          <Sidebar onNavigate={() => isCompact && setSidebarOpen(false)} />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <Outlet />
        </main>

        {/* The third column. It renders itself as a docked panel above 1440px and as an
            overlay below that, so the shell does not need to know which. */}
        <DocumentPanel />

        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      </div>
    </div>
  );
}
