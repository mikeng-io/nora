// The browser tab's title for a document page. The portal's layout titles every page
// "<page> · Nora" (src/app/[locale]/layout.tsx); a document is titled by its own title,
// not by the word "Documents", so two open documents can be told apart in the tab bar.

export const TITLE_SUFFIX = " · Nora";

/** The tab title for a document: its own title, or `fallback` (the section's name) while
 * the title is not known yet or is blank, then the site suffix. */
export function documentTabTitle(title: string | null | undefined, fallback: string): string {
  const own = (title ?? "").replace(/\s+/g, " ").trim();
  return `${own || fallback}${TITLE_SUFFIX}`;
}
