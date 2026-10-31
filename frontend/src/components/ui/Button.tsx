import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const button = cva(
  "inline-flex items-center justify-center gap-2 font-medium whitespace-nowrap select-none " +
    "transition-colors duration-[120ms] disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        primary: "bg-brand-600 text-white hover:bg-brand-700 active:bg-brand-900",
        secondary:
          "bg-surface text-ink border border-line hover:bg-sunken active:bg-line/60",
        ghost: "text-ink-muted hover:bg-sunken hover:text-ink",
        subtle: "bg-brand-50 text-brand-700 hover:bg-brand-100",
        danger: "bg-risk-bg text-risk-fg border border-risk-br hover:bg-risk-br/50",
      },
      size: {
        sm: "h-7 px-2.5 text-caption rounded-sm",
        md: "h-9 px-3.5 text-ui rounded-md",
        lg: "h-10 px-4 text-ui rounded-md",
        icon: "h-8 w-8 rounded-md",
        "icon-sm": "h-7 w-7 rounded-sm",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";
