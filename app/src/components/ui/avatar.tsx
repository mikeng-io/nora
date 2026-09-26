import Image from "next/image";
import type { CSSProperties } from "react";
import { art } from "@/components/nora/art";
import { cn } from "@/lib/cn";
import { initials, personStyle } from "@/lib/person";

// People are a disc in their own hue with their initials: no stored photo,
// no generated pattern. The hue is the same one their pin and route carry.
export function PersonAvatar({
  id,
  name,
  size = 36,
  ring = false,
  className,
  style,
}: {
  id: string;
  name: string;
  size?: number;
  ring?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative inline-grid shrink-0 place-items-center rounded-full bg-(--person) font-semibold text-surface select-none",
        ring && "ring-2 ring-(--person) ring-offset-2 ring-offset-surface",
        className,
      )}
      style={{
        ...personStyle(id),
        width: size,
        height: size,
        fontSize: Math.max(10, Math.round(size * 0.38)),
        ...style,
      }}
    >
      {initials(name)}
    </span>
  );
}

// Nora among other people's round avatars: her cut-out on a blush-wash
// disc, a 2px blush ring while she is active, a blush presence dot
// (DESIGN.md §7 "Nora in the UI"). Beside the wordmark she is bare instead:
// NoraMark.
export function NoraAvatar({
  size = 36,
  active = false,
  dot = false,
  className,
}: {
  size?: number;
  active?: boolean;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative inline-grid shrink-0 place-items-center rounded-full bg-blush-wash",
        active ? "ring-2 ring-blush" : "ring-1 ring-blush/25",
        className,
      )}
      style={{ width: size, height: size }}
    >
      <span className="absolute inset-0 overflow-hidden rounded-full">
        <Image
          src={art.icon}
          alt=""
          sizes={`${Math.round(size * 1.1)}px`}
          className="absolute top-[-12%] left-1/2 h-auto w-[110%] max-w-none -translate-x-1/2"
        />
      </span>
      {dot ? (
        <span
          className="absolute -right-px -bottom-px rounded-full bg-blush ring-2 ring-surface"
          style={{ width: Math.max(8, size * 0.26), height: Math.max(8, size * 0.26) }}
        />
      ) : null}
    </span>
  );
}

/** Her cut-out icon, bare: transparent, no disc, no ring. Beside the
 * wordmark, and anywhere she stands for the app itself. */
export function NoraMark({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <Image
      src={art.icon}
      alt=""
      sizes={`${size}px`}
      priority
      className={cn("h-auto shrink-0", className)}
      style={{ width: size }}
    />
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "font-wordmark text-[1.3125rem] leading-none font-semibold tracking-[-0.02em] text-bun",
        className,
      )}
    >
      Nora
    </span>
  );
}

/** Overlapping discs for a group ("Ken, Jo and 2 more"). */
export function AvatarStack({
  people,
  size = 28,
  max = 4,
  className,
}: {
  people: Array<{ id: string; name: string }>;
  size?: number;
  max?: number;
  className?: string;
}) {
  const shown = people.slice(0, max);
  const rest = people.length - shown.length;
  return (
    <span className={cn("flex items-center", className)}>
      {shown.map((p, i) => (
        <PersonAvatar
          key={p.id}
          id={p.id}
          name={p.name}
          size={size}
          className="ring-2 ring-surface"
          style={{ marginLeft: i === 0 ? 0 : -size * 0.28, zIndex: shown.length - i }}
        />
      ))}
      {rest > 0 ? (
        <span
          className="grid place-items-center rounded-full bg-hoodie font-mono text-[0.6875rem] text-ink-soft ring-2 ring-surface"
          style={{ width: size, height: size, marginLeft: -size * 0.28 }}
        >
          +{rest}
        </span>
      ) : null}
    </span>
  );
}
