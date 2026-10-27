import {
  BookOpenText,
  CalendarDays,
  Download,
  FileText,
  FolderOpen,
  Gamepad2,
  Map as MapIcon,
  NotebookPen,
  Sun,
  UsersRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";

// The portal's map of itself. Seven places for the circle, four for Nora.
// On a phone the bottom bar holds five (DESIGN.md §7: no raised centre button);
// People is one tap from Today and the map, and Nora is her own mark in the
// top bar. Five is the most it fits (MOBILE_NAV_MAX): a sixth would crowd the
// labels on a 320px phone, so a new place goes to the rail or replaces one.
export interface NavItem {
  key:
    | "today"
    | "map"
    | "people"
    | "plans"
    | "documents"
    | "artifacts"
    | "downloads"
    | "games"
    | "nora"
    | "memory"
    | "doctrine"
    | "abilities";
  href: string;
  icon: LucideIcon | "nora";
  /** Exact match only (a parent of other items). */
  exact?: boolean;
  mobile?: boolean;
}

/** The most destinations the phone's bottom bar holds (its grid has this many columns). */
export const MOBILE_NAV_MAX = 5;

/** The bar's grid columns by item count: literal class names, so Tailwind sees them.
 * nav.test.ts pins that there is one for every count the bar can hold. */
export const BOTTOM_NAV_COLUMNS: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-2",
  3: "grid-cols-3",
  4: "grid-cols-4",
  5: "grid-cols-5",
};

export const MAIN_NAV: NavItem[] = [
  { key: "today", href: "/today", icon: Sun, mobile: true },
  { key: "map", href: "/map", icon: MapIcon, mobile: true },
  { key: "people", href: "/people", icon: UsersRound },
  { key: "plans", href: "/plans", icon: CalendarDays, mobile: true },
  { key: "documents", href: "/documents", icon: FileText, mobile: true },
  // The files Nora sent in your rooms (ADR 0023). Not on the phone's bar, which is full.
  { key: "artifacts", href: "/artifacts", icon: FolderOpen },
  // Media the circle asked yt-dlp to fetch. Rail and palette only: the phone's bar is full.
  { key: "downloads", href: "/downloads", icon: Download },
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
