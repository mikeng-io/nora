import { ChevronLeft, ChevronRight } from "lucide-react";

// A minimal page-forward/back control for the players/activity/sessions
// list pages — link-based (not infinite scroll), matching
// game-server-monitor's own paginated pages.

export function Pagination({
  page,
  hasMore,
  onPrev,
  onNext,
  pageLabel,
}: {
  page: number;
  hasMore: boolean;
  onPrev: () => void;
  onNext: () => void;
  pageLabel: (page: number) => string;
}) {
  return (
    <div className="mt-4 flex items-center justify-between">
      <button
        type="button"
        onClick={onPrev}
        disabled={page <= 1}
        className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-text-secondary disabled:opacity-40 hover:not-disabled:bg-bg-elevated"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </button>
      <span className="font-mono text-[11px] text-text-muted">{pageLabel(page)}</span>
      <button
        type="button"
        onClick={onNext}
        disabled={!hasMore}
        className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-text-secondary disabled:opacity-40 hover:not-disabled:bg-bg-elevated"
      >
        <ChevronRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
