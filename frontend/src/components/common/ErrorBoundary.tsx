import { Component, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-dvh items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-line bg-surface p-6 text-center">
          <h1 className="text-section font-semibold text-ink">Something broke</h1>
          <p className="mt-1.5 text-ui text-ink-muted">
            The interface hit an unexpected error. Reloading usually clears it.
          </p>
          <Button variant="primary" className="mt-4" onClick={() => location.reload()}>
            Reload
          </Button>
        </div>
      </div>
    );
  }
}
