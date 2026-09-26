/** Ranks a match so a name beats a word buried in a tool's description:
 * the label itself (after its "person"/"tool" prefix) exactly, then as a
 * prefix, then a word in it, then anywhere; keywords only last. */
export function paletteScore(value: string, search: string, keywords?: string[]): number {
  const q = search.trim().toLowerCase();
  if (!q) return 1;
  const label = value.slice(value.indexOf(" ") + 1).toLowerCase();
  if (label === q) return 1;
  if (label.startsWith(q)) return 0.9;
  if (label.split(/[\s_\-·]+/).some((w) => w.startsWith(q))) return 0.75;
  if (label.includes(q)) return 0.5;
  const hay = `${label} ${(keywords ?? []).join(" ").toLowerCase()}`;
  return q.split(/\s+/).every((w) => hay.includes(w)) ? 0.2 : 0;
}
