import { cn } from "@/lib/cn";

// A static hoodie-coloured block sized to what it stands in for, no shimmer
// (DESIGN.md §7, the No-Ambience Rule). The content cross-fades in over it.
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn("block rounded-control bg-hoodie", className)} />;
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <span aria-hidden="true" className={cn("grid gap-2", className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn("h-3.5", i === lines - 1 ? "w-3/5" : "w-full")} />
      ))}
    </span>
  );
}
