import type { Tool } from "./api/nora";

// Her tool registry's declared fields (nora/web/tools.py) are machine
// vocabulary; the abilities page reads them as a few plain facts and
// filters. One mapping here, so the page and its tests agree.

export const TOOL_FILTERS = ["all", "live", "remembered", "web", "writes"] as const;
export type ToolFilter = (typeof TOOL_FILTERS)[number];

export function matchesFilter(tool: Tool, filter: ToolFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "live":
      return tool.source_tier === "confirmed";
    case "remembered":
      return tool.source_tier === "remembered";
    case "web":
      return tool.source_tier === "web";
    case "writes":
      return !tool.read_only;
  }
}

/** The key under abilities.tier.* for a source tier; an unknown or absent
 * tier is an action or a pure function, never guessed into a data source. */
export function tierKey(tier: string | null): "confirmed" | "remembered" | "web" | "action" {
  return tier === "confirmed" || tier === "remembered" || tier === "web" ? tier : "action";
}

/** The key under abilities.effect.* for a side-effect state, or null for a
 * value this build does not know (shown raw rather than mislabelled). */
export function effectKey(state: string): "none" | "confirmed" | "unknown" | null {
  return state === "none" || state === "confirmed" || state === "unknown" ? state : null;
}

/** Case-insensitive match on the name and the description. */
export function matchesQuery(text: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  return !q || q.split(/\s+/).every((w) => text.toLowerCase().includes(w));
}
