// One corpus of documents that must be accepted and documents that must be
// refused, shared by every validator we have: Zod (documents.test.ts), the
// emitted JSON Schema under ajv (same file), and Python's `jsonschema`
// (tests/unit/test_document_schema.py, which reads the committed corpus.json).
// Two validators reading one schema is how the trailing-newline and
// default-as-required disagreements got through (review, 2026-09-30); a case
// here is the cheapest way to pin one.
import { DEMOS } from "./demos/index.ts";
import { EXAMPLE } from "./emit.ts";

const page = (...children: unknown[]) => ({ version: 1, root: { type: "Page", props: { title: "T" }, children } });
const mermaid = (source: string) => page({ type: "Mermaid", props: { source, caption: "c" } });
// `levels` containers of `kind` around `inner`; the first is at depth 1.
const nest = (kind: string, levels: number, inner: Record<string, unknown>) => {
  let node = inner;
  for (let i = 0; i < levels; i++) node = { type: kind, props: kind === "Grid" ? { columns: 2 } : {}, children: [node] };
  return node;
};
const leaf = { type: "Markdown", props: { text: "x" } };
const deep = (kind: string, levels: number) => page(nest(kind, levels, leaf));

export const VALID: Record<string, unknown> = {
  example: EXAMPLE,
  ...Object.fromEntries(Object.entries(DEMOS).map(([k, v]) => [`demo ${k}`, v])),
  // What the prompt fragment tells the model: defaulted props may be left out.
  "a Badge without its tone": page({ type: "Badge", props: { text: "x" } }),
  "a Grid without its columns": page({ type: "Grid", props: {}, children: [] }),
  "a Callout without its tone": page({ type: "Callout", props: { title: "x" } }),
  "a Stat without its tone": page({ type: "Stat", props: { label: "a", value: "1" } }),
  "nesting at the depth cap (8)": deep("Grid", 7),
  "a Tabs with two Tab panels": page({
    type: "Tabs", props: {},
    children: [{ type: "Tab", props: { label: "a" }, children: [] }, { type: "Tab", props: { label: "b" }, children: [] }],
  }),
  "a plain Mermaid flowchart": mermaid("flowchart LR\n  A-->|label| B"),
  "a Mermaid node whose name merely starts like a keyword": mermaid("flowchart LR\n  style1-->link1"),
};

export const INVALID: Array<[string, unknown]> = [
  ["a className (a model-controlled class string can cover the portal)", page({ type: "Section", props: { className: "fixed inset-0" } })],
  ["an unknown component", page({ type: "Script", props: {} })],
  ["a javascript: link", page({ type: "Sources", props: { items: [{ title: "x", url: "javascript:alert(1)" }] } })],
  ["a data: link", page({ type: "Sources", props: { items: [{ title: "x", url: "data:text/html,hi" }] } })],
  ["a relative link", page({ type: "Sources", props: { items: [{ title: "x", url: "/admin" }] } })],
  ["a link with a trailing newline", page({ type: "Sources", props: { items: [{ title: "x", url: "https://a.example/\n" }] } })],
  ["an asOf with a trailing newline", page({ type: "Sources", props: { items: [{ title: "x", url: "https://a.example/", asOf: "2026-09-30\n" }] } })],
  ["an asOf in Arabic-Indic digits", page({ type: "Sources", props: { items: [{ title: "x", url: "https://a.example/", asOf: "٢٠٢٦-٠٩-٣٠" }] } })],
  ["a media code with a trailing newline", page({ type: "Image", props: { media: "abcdef12\n", alt: "a" } })],
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
  ["a Mermaid init directive", mermaid("%%{init: {'securityLevel':'loose'}}%%\nflowchart LR\nA-->B")],
  ["a Mermaid directive after the first line", mermaid("flowchart LR\nA-->B\n%%{init: {}}%%")],
  ["Mermaid frontmatter", mermaid("---\nconfig:\n  htmlLabels: true\n---\nflowchart LR\n  A-->B")],
  ["Mermaid frontmatter behind a BOM", mermaid("﻿---\nconfig:\n  htmlLabels: true\n---\nflowchart LR\n  A-->B")],
  ["a Mermaid click", mermaid('flowchart LR\n  A-->B\n  click B "//evil.example/p"')],
  ["a Mermaid click in other case", mermaid('flowchart LR\n  A-->B\n  CLICK B "//evil.example/p"')],
  ["a Mermaid style statement", mermaid("flowchart LR\n  A-->B\n  style A fill:#f00")],
  ["a Mermaid classDef", mermaid("flowchart LR\n  A-->B\n  classDef x fill:#f00\n  class A x")],
  ["a Mermaid url()", mermaid("flowchart LR\n  A-->B\n  linkStyle 0 stroke:url(https://evil.example/s)")],
  ["a Mermaid without a caption", page({ type: "Mermaid", props: { source: "flowchart LR\nA-->B" } })],
  ["a Map latitude of 91", page({ type: "Map", props: { caption: "c", places: [{ name: "x", lat: 91, lng: 0 }] } })],
  ["a Map with an address instead of coordinates", page({ type: "Map", props: { caption: "c", places: [{ name: "x", address: "1 Main St" }] } })],
  ["a one-point Map route", page({ type: "Map", props: { caption: "c", places: [{ name: "x", lat: 1, lng: 1 }], route: [{ lat: 1, lng: 1 }] } })],
  ["an Image from a URL", page({ type: "Image", props: { media: "https://example.com/x.png", alt: "a" } })],
  ["an Image with a too-short code", page({ type: "Image", props: { media: "ab12", alt: "a" } })],
  ["an Image with an uppercase code", page({ type: "Image", props: { media: "ABCDEF12", alt: "a" } })],
  ["an Image without alt text", page({ type: "Image", props: { media: "a1b2c3d4e5f6" } })],
  ["an Image with a src", page({ type: "Image", props: { media: "a1b2c3d4e5f6", alt: "a", src: "https://example.com/x.png" } })],
  ["a bad tone", page({ type: "Badge", props: { text: "x", tone: "neon" } })],
  ["five grid columns", page({ type: "Grid", props: { columns: 5 }, children: [] })],
  ["an unknown prop", page({ type: "Markdown", props: { text: "x", html: "<b>" } })],
  ["a prototype key", JSON.parse('{"version":1,"root":{"type":"Page","props":{"title":"T"},"children":[],"__proto__":{"x":1}}}')],
  ["nesting one level past the cap (9)", deep("Grid", 8)],
  [
    "a Tab one level past the cap (Tabs at 8, its Tab at 9)",
    page(nest("Grid", 7, { type: "Tabs", props: {}, children: [{ type: "Tab", props: { label: "a" }, children: [] }] })),
  ],
  ["a Card chain far past the cap (12)", deep("Card", 12)],
];
