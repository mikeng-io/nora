import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { hueOf } from "@/lib/person-color";
import { cn } from "@/lib/utils";

// The ONE place a person's identity renders as a colour + initials —
// previously duplicated (map/page.tsx's PersonRow inlined this; the P2
// dashboards' SubjectPicker used a plain colour dot instead), so the same
// person looked different depending which surface you were on. Both now
// import this.
export function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export function PersonAvatar({
  personId,
  name,
  size = "sm",
  muted = false,
  className,
}: {
  personId: string;
  name: string;
  size?: "sm" | "md";
  muted?: boolean;
  className?: string;
}) {
  const hue = hueOf(personId);
  return (
    <Avatar
      className={cn(size === "md" ? "h-8 w-8" : "h-6 w-6", "shrink-0 ring-2 transition-shadow", className)}
      style={{
        backgroundColor: `hsl(${hue} 70% 92%)`,
        // @ts-expect-error -- CSS custom value, not a token
        "--tw-ring-color": `hsl(${hue} 60% 55% / ${muted ? 0.12 : 0.35})`,
      }}
    >
      <AvatarFallback
        style={{
          color: muted ? "var(--text-muted)" : `hsl(${hue} 55% 32%)`,
          backgroundColor: "transparent",
          fontSize: size === "md" ? undefined : "9px",
        }}
      >
        {initialsOf(name)}
      </AvatarFallback>
    </Avatar>
  );
}
