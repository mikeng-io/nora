import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { COMPONENT_NAMES, documentSchema } from "./catalog";
import { DEMO_NAMES, DEMOS } from "./demos";
import { emitPrompt, emitSchema, EXAMPLE } from "./emit";

const committed = (name: string) =>
  readFileSync(fileURLToPath(new URL(`../../../../nora/documents/${name}`, import.meta.url)), "utf8");

const page = (...children: unknown[]) => ({ version: 1, root: { type: "Page", props: { title: "T" }, children } });
const accepts = (d: unknown) => documentSchema.safeParse(d).success;

describe("the emitted files match the catalog", () => {
  // A stale file means Nora's checker and the skill describe a different
  // catalog than the one the portal renders. Fix: npm run documents:emit.
  it("document.schema.json", () => expect(committed("document.schema.json")).toBe(emitSchema()));
  it("catalog-prompt.md", () => expect(committed("catalog-prompt.md")).toBe(emitPrompt()));
});

describe("the prompt's own example", () => {
  it("is a valid document, so the skill can never teach an invalid one", () => {
    expect(accepts(EXAMPLE)).toBe(true);
  });
  it("names every component in the catalog", () => {
    const prompt = emitPrompt();
    for (const name of COMPONENT_NAMES) expect(prompt).toContain(`**${name}**`);
  });
});

describe("what a document may not contain", () => {
  const cases: Array<[string, unknown]> = [
    ["a className (a model-controlled class string can cover the portal)", page({ type: "Section", props: { className: "fixed inset-0" } })],
    ["an unknown component", page({ type: "Script", props: {} })],
    ["a javascript: link", page({ type: "Sources", props: { items: [{ title: "x", url: "javascript:alert(1)" }] } })],
    ["a data: link", page({ type: "Sources", props: { items: [{ title: "x", url: "data:text/html,hi" }] } })],
    ["a relative link", page({ type: "Sources", props: { items: [{ title: "x", url: "/admin" }] } })],
    ["children on a leaf", page({ type: "Markdown", props: { text: "x" }, children: [] })],
    ["an `on` binding", page({ type: "Badge", props: { text: "x" }, on: {} })],
    ["a `repeat`", page({ type: "Badge", props: { text: "x" }, repeat: { statePath: "/a" } })],
    ["a `visible` condition", page({ type: "Badge", props: { text: "x" }, visible: true })],
    ["top-level `state`", { ...page(), state: { a: 1 } }],
    ["a Page nested in a Page", page({ type: "Page", props: { title: "x" }, children: [] })],
    ["a wrong version", { ...page(), version: 2 }],
    ["an empty title", { version: 1, root: { type: "Page", props: { title: "" }, children: [] } }],
    ["an empty Accordion", page({ type: "Accordion", props: { items: [] } })],
    ["a Stat with a className", page({ type: "Stat", props: { label: "a", value: "1", className: "x" } })],
    ["children on a Stat", page({ type: "Stat", props: { label: "a", value: "1" }, children: [] })],
    ["a Tab outside Tabs", page({ type: "Tab", props: { label: "x" }, children: [] })],
    ["a non-Tab child of Tabs", page({ type: "Tabs", props: {}, children: [{ type: "Markdown", props: { text: "x" } }] })],
    ["an empty Tabs", page({ type: "Tabs", props: {}, children: [] })],
    ["a Tab without a label", page({ type: "Tabs", props: {}, children: [{ type: "Tab", props: {}, children: [] }] })],
    ["a Code block with a className", page({ type: "Code", props: { text: "x", className: "y" } })],
    ["a one-point Chart (a single number is a Stat)", page({ type: "Chart", props: { kind: "bar", caption: "c", data: [{ label: "a", value: 1 }] } })],
    ["a Chart without a caption", page({ type: "Chart", props: { kind: "bar", data: [{ label: "a", value: 1 }, { label: "b", value: 2 }] } })],
    ["a second axis on a Chart", page({ type: "Chart", props: { kind: "line", caption: "c", y2: "x", data: [{ label: "a", value: 1 }, { label: "b", value: 2 }] } })],
    ["a pie Chart", page({ type: "Chart", props: { kind: "pie", caption: "c", data: [{ label: "a", value: 1 }, { label: "b", value: 2 }] } })],
    ["a Mermaid init directive", page({ type: "Mermaid", props: { source: "%%{init: {'securityLevel':'loose'}}%%\nflowchart LR\nA-->B", caption: "c" } })],
    ["a Mermaid directive after the first line", page({ type: "Mermaid", props: { source: "flowchart LR\nA-->B\n%%{init: {}}%%", caption: "c" } })],
    ["a Mermaid without a caption", page({ type: "Mermaid", props: { source: "flowchart LR\nA-->B" } })],
    ["a Map latitude of 91", page({ type: "Map", props: { caption: "c", places: [{ name: "x", lat: 91, lng: 0 }] } })],
    ["a Map with an address instead of coordinates", page({ type: "Map", props: { caption: "c", places: [{ name: "x", address: "1 Main St" }] } })],
    ["a one-point Map route", page({ type: "Map", props: { caption: "c", places: [{ name: "x", lat: 1, lng: 1 }], route: [{ lat: 1, lng: 1 }] } })],
    ["an Image from a URL", page({ type: "Image", props: { media: "https://example.com/x.png", alt: "a" } })],
    ["an Image with a too-short code", page({ type: "Image", props: { media: "ab12", alt: "a" } })],
    ["an Image without alt text", page({ type: "Image", props: { media: "a1b2c3d4e5f6" } })],
    ["an Image with a src", page({ type: "Image", props: { media: "a1b2c3d4e5f6", alt: "a", src: "https://example.com/x.png" } })],
    ["a bad tone", page({ type: "Badge", props: { text: "x", tone: "neon" } })],
    ["five grid columns", page({ type: "Grid", props: { columns: 5 }, children: [] })],
    ["an unknown prop", page({ type: "Markdown", props: { text: "x", html: "<b>" } })],
  ];
  for (const [label, doc] of cases) it(`rejects ${label}`, () => expect(accepts(doc)).toBe(false));

  it("accepts the ordinary case the negatives are measured against", () => {
    expect(accepts(page({ type: "Section", props: { title: "S" }, children: [{ type: "Markdown", props: { text: "hi" } }] }))).toBe(true);
  });
});

describe("the demo documents", () => {
  // They are what the skill shows the model and what the viewer is tested on,
  // so one that stops validating is a bug in both.
  it("are registered", () => expect(DEMO_NAMES.sort()).toEqual(["comparison", "evening", "report", "runbook", "trip"]));
  for (const name of DEMO_NAMES) {
    it(`${name} is a valid document`, () => {
      const r = documentSchema.safeParse(DEMOS[name]);
      expect(r.success, r.success ? "" : JSON.stringify(r.error.issues.slice(0, 3))).toBe(true);
    });
    it(`${name} states its facts with a Page title and stays under 20 KB`, () => {
      expect(DEMOS[name].root.props.title).toBeTruthy();
      expect(JSON.stringify(DEMOS[name]).length).toBeLessThan(20_000);
    });
  }
  it("use every component the catalog offers at least once", () => {
    const used = new Set<string>();
    const walk = (n: { type: string; children?: unknown[] }) => {
      used.add(n.type);
      for (const c of n.children ?? []) walk(c as { type: string; children?: unknown[] });
    };
    for (const d of Object.values(DEMOS)) walk(d.root);
    expect([...used].sort()).toEqual([...COMPONENT_NAMES].sort());
  });
});

describe("the four blocks that load or fetch something", () => {
  it("accepts a well-formed Chart, Mermaid, Map and Image", () => {
    const ok = page(
      { type: "Chart", props: { kind: "bar", caption: "c", data: [{ label: "a", value: 1 }, { label: "b", value: 2 }] } },
      { type: "Mermaid", props: { source: "flowchart LR\nA-->B", caption: "c" } },
      { type: "Map", props: { caption: "c", places: [{ name: "x", lat: 1, lng: 1 }], route: [{ lat: 1, lng: 1 }, { lat: 2, lng: 2 }] } },
      { type: "Image", props: { media: "a1b2c3d4e5f6", alt: "a" } },
    );
    expect(accepts(ok)).toBe(true);
  });
});
