import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A label above a value; the value in mono with tabular figures when it is
 * a number that changes or gets copied (DESIGN.md §4). */
export function Stat({
  label,
  value,
  hint,
  mono = true,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("grid min-w-0 content-start gap-1", className)}>
      <dt className="text-label font-medium text-ink-muted">{label}</dt>
      <dd
        className={cn(
          "truncate text-ink",
          mono ? "font-mono text-[1.0625rem] font-medium tnum" : "text-[1.0625rem] font-semibold",
        )}
      >
        {value}
      </dd>
      {hint ? <dd className="truncate text-label text-ink-muted">{hint}</dd> : null}
    </div>
  );
}

export function StatGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn("grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4", className)}>{children}</dl>;
}

/** A thin bar for a fraction: a meter to assistive tech. */
export function Meter({
  value,
  max = 1,
  label,
  tone = "denim",
  className,
}: {
  value: number;
  max?: number;
  label: string;
  tone?: "denim" | "success" | "caution" | "danger" | "person" | "ink";
  className?: string;
}) {
  const pct = Math.max(0, Math.min(1, max > 0 ? value / max : 0));
  const color = {
    denim: "bg-denim",
    success: "bg-success",
    caution: "bg-caution",
    danger: "bg-danger",
    person: "bg-(--person)",
    ink: "bg-ink-soft",
  }[tone];
  return (
    <span
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={Math.round(value * 100) / 100}
      className={cn("block h-1.5 overflow-hidden rounded-full bg-hoodie", className)}
    >
      <span
        className={cn("block h-full origin-left rounded-full transition-transform duration-240 ease-nora", color)}
        style={{ transform: `scaleX(${pct})` }}
      />
    </span>
  );
}
