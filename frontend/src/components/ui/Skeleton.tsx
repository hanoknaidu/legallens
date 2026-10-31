import { cn } from "@/lib/cn";

/** Content placeholders. Never a spinner for content — spinners hide structure. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("relative overflow-hidden rounded-sm bg-sunken", className)}
      aria-hidden="true"
    >
      <div
        className="absolute inset-0 -translate-x-full"
        style={{
          background:
            "linear-gradient(90deg, transparent, rgb(255 255 255 / 0.7), transparent)",
          animation: "shimmer 1.6s infinite",
        }}
      />
    </div>
  );
}
