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

// The page sets the title in an effect, and Next sets the route's own metadata title
// ("Documents · Nora") as it commits a navigation. Entering a document from the list with
// its data already cached renders it at once, the effect runs, and then Next writes the
// route's title over it (measured: e2e, the second visit stayed "Documents · Nora"; the
// tab can even read "" for a moment while Next swaps its <title>). So the page watches the
// title and takes it back, but only while the address is still the document's: once the
// person has navigated away, whatever the next page writes is left alone.

/** Whether the page should write its title back: something else wrote it, and the address
 * is still the one the page was opened at. */
export function shouldReassertTitle(
  current: string,
  mine: string,
  pathAtStart: string,
  pathNow: string,
): boolean {
  return current !== mine && pathNow === pathAtStart;
}

/** Whether leaving the page should put the replaced title back: only if the tab still says
 * the page's own, so a title another page already set is never overwritten. */
export function shouldRestoreTitle(current: string, mine: string): boolean {
  return current === mine;
}
