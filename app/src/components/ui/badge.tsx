import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide",
  {
    variants: {
      variant: {
        live: "border-transparent bg-success/15 text-success",
        designed: "border-border-strong text-text-muted",
        future: "border-border-strong text-text-muted",
        danger: "border-transparent bg-danger/15 text-danger",
      },
    },
    defaultVariants: {
      variant: "designed",
    },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
