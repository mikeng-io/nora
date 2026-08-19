import { cn } from "@/lib/utils";

// A pulsing placeholder block — replaces a bare "Loading…" line with
// something that reads as "content is coming" rather than "did this
// break," matching the shape of what's about to render (RoomVocabularyTeaser
// on the memory page used this same animate-pulse approach ad hoc; this is
// the shared version).
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-bg-elevated", className)} />;
}
