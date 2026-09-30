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

/** How many pages `total` documents make. Never below 1: an empty list is page one. */
export function pageCount(total: number, pageSize: number): number {
  if (!Number.isFinite(total) || !Number.isFinite(pageSize) || pageSize < 1) return 1;
  return Math.max(1, Math.ceil(total / pageSize));
}

/** Whether a document has been revised since it was made. */
export function isRevised(revision: number): boolean {
  return revision > 1;
}
