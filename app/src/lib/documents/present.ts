// The present view (design/artifact §15, §34): a document shown one top-level Section per
// slide. A view of the SAME stored spec, never a second format: each slide is the document
// with its Page kept and only some of the Page's children, so it is drawn by the same
// registry and checked by the same parseDocument.
//
// The split, which is the whole rule:
//   slide 0     the cover: the Page's title and subtitle, and every block BEFORE the
//               first Section (an opening Callout stays with the title it introduces);
//   slide 1..n  one per top-level Section, carrying every non-Section block that follows
//               it until the next Section (a block after a Section belongs to it).
// A document whose Page has no Section child has nothing to split: `planSlides` says so
// and the page shows the ordinary view with a note.

import type { DocumentNode, DocumentSpec } from "./catalog";

export type Slide = {
  /** 0 is the cover; 1..n are the Sections, in order. */
  index: number;
  kind: "cover" | "section";
  /** Indices into the Page's children that this slide shows. */
  children: number[];
  /** The Section's title, or the Page's on the cover. */
  title: string;
};

export type SlidePlan = { ok: true; slides: Slide[] } | { ok: false; reason: "no-sections" };

export function planSlides(doc: DocumentSpec): SlidePlan {
  const children = (doc.root.children ?? []) as DocumentNode[];
  const cover: Slide = { index: 0, kind: "cover", children: [], title: doc.root.props.title };
  const slides: Slide[] = [cover];
  children.forEach((child, i) => {
    if (child.type === "Section") {
      const title = String((child.props as { title?: unknown } | undefined)?.title ?? "");
      slides.push({ index: slides.length, kind: "section", children: [i], title });
    } else {
      slides[slides.length - 1].children.push(i);
    }
  });
  if (slides.length === 1) return { ok: false, reason: "no-sections" };
  return { ok: true, slides };
}

/** The document a slide draws: the same Page, with only the slide's children. */
export function slideSpec(doc: DocumentSpec, slide: Slide): DocumentSpec {
  const children = (doc.root.children ?? []) as DocumentNode[];
  return {
    ...doc,
    root: { ...doc.root, children: slide.children.map((i) => children[i]) },
  } as DocumentSpec;
}

export type SlideAction = { type: "go"; index: number } | { type: "exit" } | null;

const clamp = (i: number, count: number) => Math.min(Math.max(i, 0), count - 1);

/** What a key does on slide `index` of `count`. Arrows, Page Up/Down, space (shift for
 * back), Home and End move; Escape leaves. Anything else, and any key with a modifier
 * other than shift, is not ours (a browser shortcut must keep working). */
export function slideKey(
  key: { key: string; shiftKey?: boolean; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean },
  index: number,
  count: number,
): SlideAction {
  if (key.altKey || key.ctrlKey || key.metaKey) return null;
  switch (key.key) {
    case "ArrowRight":
    case "ArrowDown":
    case "PageDown":
      return { type: "go", index: clamp(index + 1, count) };
    case "ArrowLeft":
    case "ArrowUp":
    case "PageUp":
      return { type: "go", index: clamp(index - 1, count) };
    case " ":
    case "Spacebar":
      return { type: "go", index: clamp(index + (key.shiftKey ? -1 : 1), count) };
    case "Home":
      return { type: "go", index: 0 };
    case "End":
      return { type: "go", index: count - 1 };
    case "Escape":
      return { type: "exit" };
    default:
      return null;
  }
}

/** The query flag the Present button adds: the deck was opened from the document page, so
 * leaving goes BACK to that entry instead of pushing a second copy of it. */
export const PRESENT_FROM_PAGE = "from";

/** What owns its own keys: a field, a tab list, a scroll region (a wide table, a code
 * block), a diagram or a map, and anything marked `data-present-keys="own"`. */
export const KEYS_OWNED_BY = [
  "input",
  "textarea",
  "select",
  "[contenteditable]",
  "[role='tablist']",
  "[role='region']",
  "[role='img']",
  "pre",
  ".maplibregl-map",
  "[data-present-keys='own']",
].join(", ");

type KeyTarget = {
  closest(selector: string): unknown;
  hasAttribute(name: string): boolean;
  parentElement: KeyTarget | null;
};

/** Whether a key pressed with focus on `target` belongs to that element rather than to
 * the deck. Escape always belongs to the deck. Space and Enter press a focused button or
 * link. Arrows, space and paging keys stay with anything in `KEYS_OWNED_BY` and with any
 * element between the focus and the deck that scrolls (`scrolls`), except the deck's own
 * slide scroller (`data-present-scroller`). */
export function targetOwnsKey(
  key: string,
  target: KeyTarget | null,
  scrolls: (el: KeyTarget) => boolean,
): boolean {
  if (!target || key === "Escape") return false;
  if ((key === " " || key === "Enter") && target.closest("button, a, summary")) return true;
  if (target.closest(KEYS_OWNED_BY)) return true;
  for (let el: KeyTarget | null = target; el; el = el.parentElement) {
    if (el.hasAttribute("data-present-scroller") || el.hasAttribute("data-present-deck")) break;
    if (scrolls(el)) return true;
  }
  return false;
}

/** The smallest horizontal travel, in CSS pixels, that counts as a swipe. */
export const SWIPE_MIN_PX = 50;

/** A touch that travelled `dx`, `dy`: next (swiped left), previous (swiped right), or
 * nothing (too short, or more vertical than horizontal, which is a scroll). */
export function swipeStep(dx: number, dy: number): -1 | 0 | 1 {
  if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) <= Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}

/** The slide a `?slide=` value names, 1-based in the URL, or the cover. */
export function slideFromQuery(value: string | null | undefined, count: number): number {
  const n = Number(value);
  if (!value || !Number.isInteger(n) || n < 1) return 0;
  return clamp(n - 1, count);
}
