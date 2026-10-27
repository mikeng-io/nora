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

// The portal's map of itself. Eight places for the circle, four for Nora.
// On a phone the bottom bar holds five cells (DESIGN.md §7: no raised centre
// button): four places plus More, which opens a sheet listing everything else
// — the rest of the circle and the four pages about her. Five cells is the
// most it fits (MOBILE_NAV_MAX): a sixth would crowd the labels on a 320px
// phone, so a new place joins the sheet until it earns a bar slot.
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

/** The most cells the phone's bottom bar holds (its grid has this many columns).
 * The last cell is always More, so at most MOBILE_NAV_MAX - 1 places carry the
 * mobile flag. */
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
  // The files Nora sent in your rooms (ADR 0023). On the phone it sits in the More sheet.
  { key: "artifacts", href: "/artifacts", icon: FolderOpen },
  // Media the circle asked yt-dlp to fetch. Rail, palette and the More sheet.
  { key: "downloads", href: "/downloads", icon: Download },
  { key: "games", href: "/games", icon: Gamepad2 },
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
