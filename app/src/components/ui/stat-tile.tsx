import * as React from "react";

import { cn } from "@/lib/utils";

// Extracted from games/page.tsx's original inline `Metric` — the one place
// in the portal that already looked like a real product. Shared here so
// status/memory can match it instead of inventing their own tile shape.
export function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  className,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  hint?: string;
  className?: string;
}) {
  return (
    <div className={cn("rounded-md bg-bg-elevated p-2.5", className)} title={hint}>
      <div className="mb-1 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="text-[13px] font-medium text-text-primary">{value}</div>
    </div>
  );
}
