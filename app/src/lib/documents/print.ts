// Print and "save as PDF" (design/artifact §32): the browser's own print is the PDF path;
// there is no server-side renderer. The stylesheet is src/components/documents/print.css;
// this is the one decision in it that needs the page's text, so it is made here and
// tested without a browser.
//
// On paper a link cannot be followed, so its address is printed after it, but only where
// that adds something: a link whose visible text already IS its address (with or without
// the scheme, a `www.` or a trailing slash) would print the address twice, and a link
// that is not http(s) or mailto is never a place a reader can go.

const PRINTABLE = /^(https?:|mailto:)/i;

const bare = (s: string) =>
  s
    .trim()
    .toLowerCase()
    .replace(/^(https?:\/\/|mailto:)/, "")
    .replace(/^www\./, "")
    .replace(/\/+$/, "");

/** The address to print after a link whose text is `text`, or `null` when it would not
 * help. `mailto:` prints as the bare address. */
export function printHref(href: string | null | undefined, text: string): string | null {
  if (!href || !PRINTABLE.test(href)) return null;
  if (bare(href) === bare(text)) return null;
  return href.toLowerCase().startsWith("mailto:") ? href.slice("mailto:".length) : href;
}

/** The attribute print.css reads (`a[data-print-href]::after`). */
export const PRINT_HREF_ATTR = "data-print-href";

/** Mark every link inside `root` with the address print.css prints after it. Run just
 * before printing, so a document revised since the page loaded is marked as it is now. */
export function markLinksForPrint(root: ParentNode): void {
  root.querySelectorAll("a[href]").forEach((a) => {
    const shown = printHref(a.getAttribute("href"), a.textContent ?? "");
    if (shown) a.setAttribute(PRINT_HREF_ATTR, shown);
    else a.removeAttribute(PRINT_HREF_ATTR);
  });
}
