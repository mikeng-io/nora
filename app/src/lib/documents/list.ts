// Small pure helpers for the documents list, apart from the view so they are tested.

const PLATFORMS: Record<string, string> = {
  signal: "Signal",
  discord: "Discord",
  whatsapp: "WhatsApp",
  instagram: "Instagram",
};

/** The room's platform as a person says it. An unknown platform is shown as sent,
 * capitalised, rather than dropped or guessed at. */
export function platformLabel(platform: string): string {
  const known = PLATFORMS[platform.toLowerCase()];
  if (known) return known;
  return platform ? platform.charAt(0).toUpperCase() + platform.slice(1) : "";
}

/** How a document names where it was made: the room's own label when Nora sent one (she
 * only does for a viewer who is in that room), else the platform. Never a chat id. */
export function roomName(doc: { roomLabel?: string; platform: string }): string {
  const label = (doc.roomLabel ?? "").trim();
  return label || platformLabel(doc.platform);
}

/** How many pages `total` documents make. Never below 1: an empty list is page one. */
export function pageCount(total: number, pageSize: number): number {
  if (!Number.isFinite(total) || !Number.isFinite(pageSize) || pageSize < 1) return 1;
  return Math.max(1, Math.ceil(total / pageSize));
}

/** Whether a document has been revised since it was made. */
export function isRevised(revision: number): boolean {
  return revision > 1;
}

// ------------------------------------------------------------------- search

/** The longest search Nora accepts (nora.domain.documents.DOCUMENT_QUERY_MAX): a longer
 * one is a 400 there, so it is cut here instead of failing the whole list. */
export const SEARCH_MAX = 200;
/** How long typing must pause before the list is asked again. */
export const SEARCH_DEBOUNCE_MS = 300;

/** The text as it is sent: trimmed, inner whitespace collapsed, at most SEARCH_MAX
 * characters. Empty means "no search". The same rule Nora applies on her side. */
export function normalizeSearch(text: string): string {
  return text.replace(/\s+/g, " ").trim().slice(0, SEARCH_MAX).trim();
}

// ---------------------------------------------------------------- freshness

/** What a list or a document on screen can honestly say about itself.
 * - `loading`: the rows shown belong to the PREVIOUS filter, page or search, and the new
 *   ones are on the way, so the old list must not look current.
 * - `outdated`: the last read failed and these rows are what an earlier read left.
 * - `current`: nothing to say. */
export type Freshness = "loading" | "outdated" | "current";

export function freshness(q: { isPlaceholderData?: boolean; isError: boolean; hasData: boolean }): Freshness {
  if (q.isPlaceholderData && q.hasData) return "loading";
  if (q.isError && q.hasData) return "outdated";
  return "current";
}

/** Whether the list is waiting on the person's own typing: what is in the box is not yet
 * what was asked, so the rows shown answer an older question. */
export function isTyping(text: string, asked: string): boolean {
  return normalizeSearch(text) !== asked;
}

/** The one-time notice a delete leaves on the list. The page that deleted it navigates to
 * `/documents?deleted=1`; the list shows the notice while the flag is there. */
export const DELETED_FLAG = "deleted";

export function deletedNoticeShown(search: string): boolean {
  return new URLSearchParams(search).get(DELETED_FLAG) === "1";
}
