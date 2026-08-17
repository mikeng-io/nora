import { cn } from "@/lib/utils";

// A reachable dot pulses (ping ring) so "live" reads as alive, not a static
// icon that could just as easily be a screenshot. `title` carries the
// hover-reveal detail — same copy already shown below it, just also
// available on hover for anyone scanning quickly.
export function StatusDot({ reachable, title }: { reachable: boolean; title?: string }) {
  return (
    <span className="relative inline-flex h-1.5 w-1.5 shrink-0" title={title}>
      {reachable && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
      )}
      <span
        className={cn(
          "relative inline-flex h-1.5 w-1.5 rounded-full",
          reachable ? "bg-success" : "bg-danger",
        )}
      />
    </span>
  );
}
