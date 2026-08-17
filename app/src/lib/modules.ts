/**
 * The module registry (design spec §3.1) — the ONE place a nav entry gets
 * declared. Game hub and future modules (Life360 map, 1-on-1 chat) each add
 * one entry here when they're built; nothing else in the portal shell
 * changes.
 */
export interface Module {
  id: string;
  label: string;
  href: string;
  status: "live" | "designed" | "future";
}

export const MODULES: Module[] = [
  { id: "status", label: "Status", href: "/status", status: "live" },
  { id: "memory", label: "Memory", href: "/memory", status: "live" },
];
