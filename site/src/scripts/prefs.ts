// The visitor's two choices, lighting and language, each remembered per browser.
//
// Lighting: "Studio" (light) and "Late night" (dark), DESIGN.md's two themes.
// With no stored choice the page follows the system; a choice is remembered
// per browser. The swap is a cross-fade (a view transition, 240ms), which
// DESIGN.md §6 keeps under reduced motion too, since it is the state change
// itself, not decoration.
type Theme = "light" | "dark";

const root = document.documentElement;
const system = matchMedia("(prefers-color-scheme: dark)");
const THEME_COLOR: Record<Theme, string> = { light: "#f4f2f2", dark: "#141112" };

function current(): Theme {
  const set = root.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return system.matches ? "dark" : "light";
}

function sync(): void {
  const now = current();
  for (const b of document.querySelectorAll<HTMLElement>("[data-theme-set]")) {
    b.setAttribute("aria-pressed", String(b.dataset.themeSet === now));
  }
  for (const b of document.querySelectorAll<HTMLElement>("[data-theme-toggle]")) {
    const label = now === "dark" ? b.dataset.labelDark : b.dataset.labelLight;
    if (label) b.setAttribute("aria-label", label);
  }
  // A hand-picked lighting overrides the system's: re-point the art that
  // is chosen by `prefers-color-scheme` (the hero portrait) and the
  // browser chrome colour.
  const chosen = Boolean(root.dataset.theme);
  for (const s of document.querySelectorAll<HTMLSourceElement>('source[data-scheme="dark"]')) {
    s.media = chosen ? (now === "dark" ? "all" : "not all") : "(prefers-color-scheme: dark)";
  }
  for (const img of document.querySelectorAll<HTMLImageElement>("img[data-alt-light]")) {
    img.alt = (now === "dark" ? img.dataset.altDark : img.dataset.altLight) ?? img.alt;
  }
  if (chosen) {
    for (const m of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
      m.content = THEME_COLOR[now];
    }
  }
}

/** Resolves when the re-pointed art has decoded, or after `ms`, whichever is first. */
function artReady(ms: number): Promise<void> {
  const decoding = [...document.querySelectorAll<HTMLImageElement>("picture[data-lit] img")].map((img) =>
    img.decode().catch(() => undefined),
  );
  return Promise.race([Promise.all(decoding).then(() => undefined), new Promise<void>((r) => setTimeout(r, ms))]);
}

function apply(next: Theme): void {
  if (next === current() && root.dataset.theme) return;
  // The new lighting is captured once her portrait has decoded, so the
  // cross-fade lands on the new picture rather than a half-loaded one.
  const swap = async () => {
    root.dataset.theme = next;
    try {
      localStorage.setItem("nora-theme", next);
    } catch {
      /* private window: the choice lasts for this page only */
    }
    sync();
    await artReady(1200);
  };
  if ("startViewTransition" in document) document.startViewTransition(swap);
  else void swap();
}

document.addEventListener("click", (event) => {
  // Language: a link marked data-lang records the choice, so the zh-Hant home
  // stops sending this browser to /en/ (layouts/Base.astro, `negotiate`).
  const lang = (event.target as Element).closest<HTMLElement>("[data-lang]");
  if (lang?.dataset.lang) {
    try {
      localStorage.setItem("nora-lang", lang.dataset.lang);
    } catch {
      /* no storage: the link still goes where it says */
    }
  }
  const target = (event.target as Element).closest<HTMLElement>("[data-theme-set], [data-theme-toggle]");
  if (!target) return;
  if (target.dataset.themeSet === "light" || target.dataset.themeSet === "dark") apply(target.dataset.themeSet);
  else apply(current() === "dark" ? "light" : "dark");
});

system.addEventListener("change", sync);
sync();

export {};
