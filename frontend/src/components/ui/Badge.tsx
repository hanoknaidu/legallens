import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const badge = cva(
  "inline-flex items-center gap-1 font-medium rounded-sm border whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "bg-sunken text-ink-muted border-line",
        brand: "bg-brand-50 text-brand-700 border-brand-100",
        grounded: "bg-grounded-bg text-grounded-fg border-grounded-br",
        attention: "bg-attention-bg text-attention-fg border-attention-br",
        risk: "bg-risk-bg text-risk-fg border-risk-br",
        processing: "bg-processing-bg text-processing-fg border-processing-bg",
      },
      size: {
        sm: "h-5 px-1.5 text-micro",
        md: "h-6 px-2 text-caption",
      },
    },
    defaultVariants: { tone: "neutral", size: "sm" },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badge> {}

export function Badge({ className, tone, size, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone, size }), className)} {...props} />;
}
