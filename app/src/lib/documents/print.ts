// Print and "save as PDF" (design/artifact §34): the browser's own print is the PDF path;
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

/** How long the Print button waits for diagrams to redraw in the light before printing
 * anyway (print.css then inverts any still drawn dark). */
export const LIGHT_REDRAW_TIMEOUT_MS = 4000;

/**
 * Print on light paper whatever the theme. print.css gives the page the light tokens, but
 * a Mermaid diagram copied the theme's colours into its SVG when it was drawn, so in the
 * dark theme the button first switches the page to light, waits until every diagram in the
 * document says it was drawn light (`data-mermaid-theme`), prints, and puts the theme back.
 * The browser's own print menu cannot wait; for it print.css inverts a dark-drawn diagram.
 */
export async function printInLight(opts: {
  html: { dataset: DOMStringMap };
  drawnThemes: () => string[];
  print: () => void;
  wait?: (ms: number) => Promise<void>;
  timeoutMs?: number;
}): Promise<void> {
  const { html, drawnThemes, print } = opts;
  const wait = opts.wait ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const timeout = opts.timeoutMs ?? LIGHT_REDRAW_TIMEOUT_MS;
  const before = html.dataset.theme;
  const dark = before === "dark";
  if (dark) html.dataset.theme = "light";
  try {
    if (dark) {
      for (let waited = 0; waited < timeout && drawnThemes().some((t) => t !== "light"); waited += 50) {
        await wait(50);
      }
    }
    print();
  } finally {
    if (dark) html.dataset.theme = before;
  }
}
