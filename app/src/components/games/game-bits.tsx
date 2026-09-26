"use client";

import { useTranslations } from "next-intl";
import { Gamepad2, RotateCw, Save } from "lucide-react";
import { Badge, Dot } from "@/components/ui/badge";
import type { ServerState } from "@/lib/games";
import { cn } from "@/lib/cn";

/** A server's mark: its own colour (Pulse's, the server's identity, not a
 * design token), a controller glyph, rounded like every control. */
export function ServerMark({ color, size = 40, className }: { color: string; size?: number; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("grid shrink-0 place-items-center rounded-control text-white", className)}
      style={{ background: color, width: size, height: size }}
    >
      <Gamepad2 style={{ width: size * 0.45, height: size * 0.45 }} />
    </span>
  );
}

/** Online, offline, restarting or a shared save: a word and a mark, never
 * colour alone. The dot pulses only while someone is actually playing. */
export function ServerStateBadge({ state, playing = false }: { state: ServerState; playing?: boolean }) {
  const t = useTranslations("games.state");
  if (state === "local")
    return (
      <Badge icon={<Save aria-hidden="true" />} tone="neutral">
        {t("local")}
      </Badge>
    );
  if (state === "restarting")
    return (
      <Badge icon={<RotateCw aria-hidden="true" />} tone="caution">
        {t("restarting")}
      </Badge>
    );
  return (
    <Badge tone={state === "online" ? "success" : "neutral"} icon={<Dot tone={state === "online" ? "success" : "muted"} live={playing} />}>
      {t(state === "online" ? "online" : state === "offline" ? "offline" : "unknown")}
    </Badge>
  );
}

/** A player's name chip: players are Steam accounts, not people, so they
 * get a neutral disc; a player linked to someone in the circle shows that
 * person instead (see PlayerIdentity). */
export function PlayerChip({ name, online = false }: { name: string; online?: boolean }) {
  return (
    <span className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border border-hairline bg-studio pr-2.5 pl-1 text-meta font-medium text-ink">
      <span className="relative grid size-5 shrink-0 place-items-center rounded-full bg-hoodie text-[0.625rem] font-semibold text-ink-soft">
        {name.slice(0, 1).toUpperCase()}
        {online ? <span className="absolute -right-0.5 -bottom-0.5 size-2 rounded-full bg-success ring-2 ring-studio" /> : null}
      </span>
      <span className="truncate">{name}</span>
    </span>
  );
}
