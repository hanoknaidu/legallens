import { TopBar } from "@/components/shell/TopBar";
import { Badge } from "@/components/ui/Badge";

const SHORTCUTS = [
  ["⌘ N", "New conversation"],
  ["⌘ B", "Toggle sidebar"],
  ["Enter", "Send message"],
  ["Shift + Enter", "New line"],
  ["Esc", "Close panel"],
];

export function SettingsPage() {
  return (
    <>
      <TopBar title="Settings" />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-6 px-6 py-6">
          <section className="rounded-lg border border-line bg-surface p-5">
            <h2 className="text-ui font-semibold text-ink">Keyboard shortcuts</h2>
            <dl className="mt-3 divide-y divide-line">
              {SHORTCUTS.map(([key, label]) => (
                <div key={key} className="flex items-center justify-between py-2">
                  <dt className="text-ui text-ink-muted">{label}</dt>
                  <dd>
                    <kbd className="rounded-sm border border-line bg-sunken px-2 py-0.5 font-mono text-caption text-ink-muted">
                      {key}
                    </kbd>
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="rounded-lg border border-line bg-surface p-5">
            <h2 className="text-ui font-semibold text-ink">About this build</h2>
            <div className="mt-3 space-y-2 text-ui text-ink-muted">
              <p className="flex items-center gap-2">
                <Badge tone="attention">UI preview</Badge>
                All data is mocked in the browser. No backend is connected.
              </p>
              <p>
                Answers, citations and documents come from local fixtures. Nothing is uploaded, and
                no model is called.
              </p>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
