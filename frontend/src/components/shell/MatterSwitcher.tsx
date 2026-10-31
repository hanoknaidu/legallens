import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Briefcase } from "lucide-react";
import { api } from "@/lib/api";
import { useUIStore } from "@/stores/useUIStore";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/Menu";

export function MatterSwitcher() {
  const { activeMatterId, setActiveMatter } = useUIStore();
  const { data: matters = [] } = useQuery({ queryKey: ["matters"], queryFn: api.matters });
  const active = matters.find((m) => m.id === activeMatterId);

  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          className="group flex w-full items-center gap-2 rounded-md border border-transparent px-2 py-1.5
                     text-left transition-colors duration-[120ms] hover:border-line hover:bg-surface"
        >
          <Briefcase size={14} className="shrink-0 text-ink-subtle" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-small font-medium text-ink">
              {active?.name ?? "All matters"}
            </span>
            <span className="block truncate text-micro text-ink-subtle">
              {active?.client ?? `${matters.length} matters`}
            </span>
          </span>
          <ChevronsUpDown size={13} className="shrink-0 text-ink-subtle" />
        </button>
      </MenuTrigger>
      <MenuContent align="start">
        <MenuItem onSelect={() => setActiveMatter(null)}>
          <Check size={14} className={activeMatterId ? "opacity-0" : "text-brand-600"} />
          All matters
        </MenuItem>
        <MenuSeparator />
        {matters.map((m) => (
          <MenuItem key={m.id} onSelect={() => setActiveMatter(m.id)}>
            <Check
              size={14}
              className={activeMatterId === m.id ? "text-brand-600" : "opacity-0"}
            />
            <span className="truncate">{m.name}</span>
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}
