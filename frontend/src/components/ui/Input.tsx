import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "h-9 w-full rounded-md border border-line bg-surface px-3 text-ui text-ink",
        "placeholder:text-ink-subtle transition-colors duration-[120ms]",
        "hover:border-line-strong focus:border-brand-500",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
