/**
 * The module registry (design spec §3.1) — the ONE place a nav entry gets
 * declared. Game hub and future modules (Life360 map, 1-on-1 chat) each add
 * one entry here when they're built; nothing else in the portal shell
 * changes. `labelKey` names a key in messages/*.json's "nav" namespace
 * rather than a literal string, so a new module carries translations from
 * the start instead of needing extraction later.
 */
export interface Module {
  id: string;
  labelKey: "status" | "memory" | "games" | "map" | "events" | "people" | "doctrine";
  href: string;
  status: "live" | "designed" | "future";
}

export const MODULES: Module[] = [
  { id: "status", labelKey: "status", href: "/status", status: "live" },
  { id: "memory", labelKey: "memory", href: "/memory", status: "live" },
  { id: "games", labelKey: "games", href: "/games", status: "live" },
  { id: "map", labelKey: "map", href: "/map", status: "live" },
  { id: "people", labelKey: "people", href: "/people", status: "live" },
  // Now participant-scoped (not conversation-scoped), so there's no
  // group-picker gap left to block this — flipped from "designed" once
  // fetchEvents() stopped needing a conversation_id.
  { id: "events", labelKey: "events", href: "/events", status: "live" },
  // Graduated out of status page's placeholder capability tiles (same as
  // skills did) once it got a real page of its own.
  { id: "doctrine", labelKey: "doctrine", href: "/doctrine", status: "live" },
];
