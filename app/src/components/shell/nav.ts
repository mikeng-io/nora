import {
  BookOpenText,
  CalendarDays,
  Gamepad2,
  Map as MapIcon,
  NotebookPen,
  Sun,
  UsersRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";

// The portal's map of itself. Five places for the circle, four for Nora.
// On a phone the bottom bar holds four (DESIGN.md §7: four destinations, no
// raised centre button); People is one tap from Today and the map, and
// Nora is her own mark in the top bar.
export interface NavItem {
  key: "today" | "map" | "people" | "plans" | "games" | "nora" | "memory" | "doctrine" | "abilities";
  href: string;
  icon: LucideIcon | "nora";
  /** Exact match only (a parent of other items). */
  exact?: boolean;
  mobile?: boolean;
}

export const MAIN_NAV: NavItem[] = [
  { key: "today", href: "/today", icon: Sun, mobile: true },
  { key: "map", href: "/map", icon: MapIcon, mobile: true },
  { key: "people", href: "/people", icon: UsersRound },
  { key: "plans", href: "/plans", icon: CalendarDays, mobile: true },
  { key: "games", href: "/games", icon: Gamepad2, mobile: true },
];

export const NORA_NAV: NavItem[] = [
  { key: "nora", href: "/nora", icon: "nora", exact: true },
  { key: "memory", href: "/nora/memory", icon: NotebookPen },
  { key: "doctrine", href: "/nora/doctrine", icon: BookOpenText },
  { key: "abilities", href: "/nora/abilities", icon: Wrench },
];

export function isActive(pathname: string, item: NavItem): boolean {
  if (item.exact) return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}
