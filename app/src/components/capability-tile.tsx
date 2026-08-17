import * as React from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// A placeholder for a capability Nora has (or could plausibly have) that
// isn't wired to the portal yet. Deliberately real UI, not a TODO comment —
// the point is to show where this product is going, not just what's live
// this second. `variant` picks Badge's existing "designed" (real backend
// data exists, just not surfaced) vs "future" (would need new backend work).
export function CapabilityTile({
  icon: Icon,
  title,
  description,
  badgeLabel,
  variant = "future",
}: {
  icon: React.ElementType;
  title: string;
  description: string;
  badgeLabel: string;
  variant?: "designed" | "future";
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2 space-y-0 p-4 pb-2">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 shrink-0 text-text-muted" />
          <CardTitle className="text-[13px]">{title}</CardTitle>
        </div>
        <Badge variant={variant} className="shrink-0">
          {badgeLabel}
        </Badge>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <p className="text-[12.5px] text-text-secondary">{description}</p>
      </CardContent>
    </Card>
  );
}
