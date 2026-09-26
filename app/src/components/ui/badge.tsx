import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "neutral" | "success" | "caution" | "danger" | "denim" | "nora" | "outline";

const tones: Record<Tone, string> = {
  neutral: "bg-hoodie text-ink-soft",
  outline: "border border-hairline bg-surface text-ink-soft",
  // A state is never colour alone (DESIGN.md §3): these carry a word, and
  // the caller adds an icon where the badge stands by itself.
  success: "bg-hoodie text-success",
  caution: "bg-hoodie text-caution",
  danger: "bg-hoodie text-danger",
  denim: "bg-hoodie text-denim",
  // Blush means Nora and nothing else.
  nora: "bg-blush-wash text-ink",
};

export function Badge({
  tone = "neutral",
  icon,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone; icon?: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 max-w-full items-center gap-1.5 justify-self-start rounded-full px-2.5 text-label font-medium whitespace-nowrap",
        "[&_svg]:size-3.5 [&_svg]:shrink-0",
        tones[tone],
        className,
      )}
      {...props}
    >
      {icon}
      <span className="truncate">{children}</span>
    </span>
  );
}

/** A small round state mark before a word. Colour reinforces the word. */
export function Dot({
  tone,
  live = false,
  className,
}: {
  tone: "success" | "caution" | "danger" | "muted" | "nora" | "person";
  live?: boolean;
  className?: string;
}) {
  const color = {
    success: "bg-success",
    caution: "bg-caution",
    danger: "bg-danger",
    muted: "bg-seam",
    nora: "bg-blush",
    person: "bg-(--person)",
  }[tone];
  return (
    <span className={cn("relative inline-flex size-2 shrink-0", className)} aria-hidden="true">
      {live ? (
        <span data-live-mark className={cn("absolute inset-0 rounded-full motion-safe:animate-live", color)} />
      ) : null}
      <span className={cn("relative inline-flex size-2 rounded-full", color)} />
    </span>
  );
}
