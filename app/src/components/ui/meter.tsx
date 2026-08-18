import { cn } from "@/lib/utils";

// A labeled gauge for a bounded stat (survival vitals, limb health) — pairs
// color with a redundant numeric channel so severity never reads by color
// alone (game-server-monitor's own accessibility convention: color-only
// signal is never trusted as the sole indicator).

function severity(fraction: number, invert: boolean): "good" | "warn" | "bad" {
  const f = invert ? 1 - fraction : fraction;
  if (f >= 0.5) return "good";
  if (f >= 0.2) return "warn";
  return "bad";
}

const SEVERITY_CLASS: Record<string, string> = {
  good: "bg-success",
  warn: "bg-accent",
  bad: "bg-danger",
};

export function Meter({
  label,
  value,
  max = 100,
  /** True when a HIGHER value is worse (radiation, fatigue) — flips which
   * end of the bar reads as healthy. */
  invert = false,
  valueLabel,
}: {
  label: string;
  value: number;
  max?: number;
  invert?: boolean;
  valueLabel?: string;
}) {
  const fraction = Math.max(0, Math.min(1, value / max));
  const level = severity(fraction, invert);

  return (
    <div>
      <div className="mb-1 flex items-center justify-between font-mono text-[10px] uppercase tracking-wide text-text-muted">
        <span>{label}</span>
        <span>{valueLabel ?? Math.round(value)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-bg-elevated">
        <div
          className={cn("h-full rounded-full transition-[width]", SEVERITY_CLASS[level])}
          style={{ width: `${fraction * 100}%` }}
        />
      </div>
    </div>
  );
}
