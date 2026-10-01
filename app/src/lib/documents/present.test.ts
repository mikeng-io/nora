import { describe, expect, it } from "vitest";
import { parseDocument, type DocumentSpec } from "./catalog";
import { planSlides, slideFromQuery, slideKey, slideSpec, swipeStep, SWIPE_MIN_PX } from "./present";

// The present view's two rules a browser cannot be trusted to show: how a document splits
// into slides, and what each key does (design/artifact §32).

const md = (text: string) => ({ type: "Markdown", props: { text } });
const section = (title: string, ...children: unknown[]) => ({ type: "Section", props: { title }, children });
const callout = (title: string) => ({ type: "Callout", props: { title, tone: "info" } });

function doc(children: unknown[]): DocumentSpec {
  const parsed = parseDocument({ version: 1, root: { type: "Page", props: { title: "Trip" }, children } });
  if (!parsed.ok) throw new Error(parsed.reason);
  return parsed.doc;
}

describe("planSlides", () => {
  it("makes a cover, then one slide per top-level Section, in order", () => {
    const plan = planSlides(doc([section("Day 1", md("a")), section("Day 2", md("b")), section("Day 3")]));
    expect(plan.ok).toBe(true);
    if (!plan.ok) return;
    expect(plan.slides.map((s) => [s.index, s.kind, s.title, s.children])).toEqual([
      [0, "cover", "Trip", []],
      [1, "section", "Day 1", [0]],
      [2, "section", "Day 2", [1]],
      [3, "section", "Day 3", [2]],
    ]);
  });

  it("keeps a block before the first Section on the cover, and a block after a Section with it", () => {
    const plan = planSlides(doc([callout("Read me"), section("One"), md("after one"), callout("also"), section("Two")]));
    if (!plan.ok) throw new Error("expected slides");
    expect(plan.slides.map((s) => s.children)).toEqual([[0], [1, 2, 3], [4]]);
  });

  it("never splits inside a Section: a nested Section stays on its parent's slide", () => {
    const plan = planSlides(doc([section("Outer", section("Inner", md("x")), md("y"))]));
    if (!plan.ok) throw new Error("expected slides");
    expect(plan.slides).toHaveLength(2);
  });

  it("refuses a document with no top-level Section, which the page then shows normally", () => {
    expect(planSlides(doc([md("only prose"), callout("and a note")]))).toEqual({ ok: false, reason: "no-sections" });
    expect(planSlides(doc([]))).toEqual({ ok: false, reason: "no-sections" });
  });

  it("draws each slide as a valid document holding only its own blocks", () => {
    const d = doc([callout("Intro"), section("One", md("first")), section("Two", md("second"))]);
    const plan = planSlides(d);
    if (!plan.ok) throw new Error("expected slides");
    const two = slideSpec(d, plan.slides[2]);
    expect(parseDocument(two).ok).toBe(true);
    expect(two.root.props.title).toBe("Trip");
    expect(JSON.stringify(two.root.children)).toContain("second");
    expect(JSON.stringify(two.root.children)).not.toContain("first");
    expect(slideSpec(d, plan.slides[0]).root.children).toEqual([d.root.children[0]]);
  });
});

describe("slideKey", () => {
  const at = (key: string, index = 1, extra: Record<string, boolean> = {}) => slideKey({ key, ...extra }, index, 3);

  it("moves forward on right, down, page down and space, and back on their opposites", () => {
    for (const k of ["ArrowRight", "ArrowDown", "PageDown", " "]) expect(at(k), k).toEqual({ type: "go", index: 2 });
    for (const k of ["ArrowLeft", "ArrowUp", "PageUp"]) expect(at(k), k).toEqual({ type: "go", index: 0 });
    expect(at(" ", 1, { shiftKey: true })).toEqual({ type: "go", index: 0 });
  });

  it("stops at both ends instead of wrapping", () => {
    expect(at("ArrowRight", 2)).toEqual({ type: "go", index: 2 });
    expect(at("ArrowLeft", 0)).toEqual({ type: "go", index: 0 });
    expect(at("Home", 2)).toEqual({ type: "go", index: 0 });
    expect(at("End", 0)).toEqual({ type: "go", index: 2 });
  });

  it("leaves on Escape", () => {
    expect(at("Escape")).toEqual({ type: "exit" });
  });

  it("leaves every other key, and every browser shortcut, alone", () => {
    expect(at("a")).toBeNull();
    expect(at("Tab")).toBeNull();
    expect(at("ArrowRight", 1, { metaKey: true })).toBeNull();
    expect(at("ArrowLeft", 1, { altKey: true })).toBeNull();
    expect(at(" ", 1, { ctrlKey: true })).toBeNull();
  });
});

describe("swipeStep and the slide in the URL", () => {
  it("reads a long horizontal swipe as a step, and a short or vertical one as nothing", () => {
    expect(swipeStep(-SWIPE_MIN_PX, 0)).toBe(1);
    expect(swipeStep(SWIPE_MIN_PX + 20, 10)).toBe(-1);
    expect(swipeStep(-(SWIPE_MIN_PX - 1), 0)).toBe(0);
    expect(swipeStep(-80, 120)).toBe(0);
  });

  it("reads ?slide= as 1-based and clamps it, and anything else as the cover", () => {
    expect(slideFromQuery("2", 4)).toBe(1);
    expect(slideFromQuery("99", 4)).toBe(3);
    for (const bad of [null, "", "0", "-1", "x", "1.5"]) expect(slideFromQuery(bad, 4), String(bad)).toBe(0);
  });
});
