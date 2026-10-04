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
  "a plain Mermaid flowchart": mermaid("flowchart LR\n  A-->|label| B"),
  "a Mermaid node whose name merely starts like a keyword": mermaid("flowchart LR\n  style1-->link1"),
  "a Mermaid flowchart with Cantonese labels": mermaid("flowchart LR\n  A[開始] -->|去機場| B[完成]"),
  "a Mermaid style line with hex colours": mermaid("flowchart TD\n  Start --> Stop\n  style Start fill:#f9f,stroke:#333,stroke-width:2px"),
  "a Mermaid classDef and :::": mermaid("flowchart LR\n  A:::hot-->B\n  classDef hot fill:#f96"),
  "a Mermaid linkStyle": mermaid("flowchart LR\n  A-->B\n  linkStyle 0 stroke:#ff3"),
  "a Mermaid node that is named like a statement": mermaid("flowchart LR\n  call[Call the hotel] --> book[Book]"),
  "a Mermaid sequence diagram": mermaid("sequenceDiagram\n  Alice->>Bob: Hello\n  Bob-->>Alice: Hi"),
  "a Mermaid gantt": mermaid("gantt\n  title Plan\n  section Trip\n  Flights :a1, 2026-12-01, 3d"),
  "a Progress at its max": page({ type: "Progress", props: { label: "a", value: 5, max: 5 } }),
  "a Countdown without a time": page({ type: "Countdown", props: { label: "a", date: "2027-06-12" } }),
  "a Seating without a capacity": page({ type: "Seating", props: { caption: "c", tables: [{ name: "T1", seats: [] }] } }),
  "a Contact with an international phone": page({ type: "Contact", props: { items: [{ name: "a", phone: "+852 5550-0101" }] } }),
  "a donut Chart": page({ type: "Chart", props: { kind: "donut", caption: "c", data: [{ label: "a", value: 3 }, { label: "b", value: 1 }] } }),
  "a Chart value at the bound": page({ type: "Chart", props: { kind: "bar", caption: "c", data: [{ label: "a", value: 1e15 }, { label: "b", value: -1e15 }] } }),
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
  ["a Tabs (removed: nothing used it and it needed three schema branches, design §42)", page({ type: "Tabs", props: {}, children: [] })],
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
  ["a Mermaid url()", mermaid("flowchart LR\n  A-->B\n  linkStyle 0 stroke:url(https://evil.example/s)")],
  ["Mermaid frontmatter behind a non-breaking space", mermaid("\u00A0---\nconfig:\n  htmlLabels: true\n\u00A0---\nflowchart LR\n  A-->B")],
  ["Mermaid frontmatter behind a vertical tab", mermaid("\u000B---\nconfig:\n  htmlLabels: true\n\u000B---\nflowchart LR\n  A-->B")],
  ["Mermaid frontmatter behind an ideographic space", mermaid("\u3000---\nconfig:\n  htmlLabels: true\n\u3000---\nflowchart LR\n  A-->B")],
  ["Mermaid frontmatter behind a line separator", mermaid("\u2028---\nconfig:\n  htmlLabels: true\n\u2028---\nflowchart LR\n  A-->B")],
  ["a Mermaid source that starts with a space", mermaid(" flowchart LR\n  A-->B")],
  ["a Mermaid source that starts with a newline", mermaid("\nflowchart LR\n  A-->B")],
  ["a Mermaid source with no diagram keyword", mermaid("A-->B")],
  ["a Mermaid journey (mermaid draws it with HTML)", mermaid("journey\n  title x\n  section a\n    t: 5: me")],
  ["a Mermaid CSS escape hiding url(", mermaid("flowchart LR\n  A-->B\n  style A fill:u\\72 l(https://evil.example/esc)")],
  ["a Mermaid click after a semicolon", mermaid('flowchart LR; A-->B; click B "https://evil.example/semi"')],
  ["a Mermaid click after a lone CR", mermaid('flowchart LR\r  A-->B\r  click B "https://evil.example/cr"')],
  ["a Mermaid click indented with a non-breaking space", mermaid('flowchart LR\n  A-->B\n\u00A0 click B "https://evil.example/n"')],
  ["a Mermaid sequence links statement", mermaid('sequenceDiagram\n  participant A\n  links A: {"x": "https://evil.example/sq"}\n  A->>A: hi')],
  ["a Mermaid node image", mermaid('flowchart LR\n  A@{ img: "https://evil.example/x.png", shape: imageSquare }-->B')],
  ["a Mermaid style line with a function", mermaid("flowchart LR\n  A-->B\n  style A fill:rgb(1,2,3)")],
  ["a Mermaid style line with an entity", mermaid("flowchart LR\n  A-->B\n  style A fill:u&#114;l(x)")],
  ["a Mermaid next-line character hiding a click", mermaid("flowchart LR\u0085 click B x")],
  ["a Chart value past the bound", page({ type: "Chart", props: { kind: "bar", caption: "c", data: [{ label: "a", value: 1e16 }, { label: "b", value: 2 }] } })],
  ["a link with a non-breaking character", page({ type: "Sources", props: { items: [{ title: "x", url: "https://a.example/\u00A0b" }] } })],
  ["a link with a byte-order mark", page({ type: "Sources", props: { items: [{ title: "x", url: "https://a.example/\uFEFFb" }] } })],
  ["a link with a control character", page({ type: "Sources", props: { items: [{ title: "x", url: "https://a.example/\u001Cb" }] } })],
  ["a Mermaid without a caption", page({ type: "Mermaid", props: { source: "flowchart LR\nA-->B" } })],
  ["a Map latitude of 91", page({ type: "Map", props: { caption: "c", places: [{ name: "x", lat: 91, lng: 0 }] } })],
  ["a Map with an address instead of coordinates", page({ type: "Map", props: { caption: "c", places: [{ name: "x", address: "1 Main St" }] } })],
  ["a one-point Map route", page({ type: "Map", props: { caption: "c", places: [{ name: "x", lat: 1, lng: 1 }], route: [{ lat: 1, lng: 1 }] } })],
  ["an Image from a URL", page({ type: "Image", props: { media: "https://example.com/x.png", alt: "a" } })],
  ["an Image with a too-short code", page({ type: "Image", props: { media: "ab12", alt: "a" } })],
  ["an Image with an uppercase code", page({ type: "Image", props: { media: "ABCDEF12", alt: "a" } })],
  ["an Image without alt text", page({ type: "Image", props: { media: "a1b2c3d4e5f6" } })],
  ["an Image with a src", page({ type: "Image", props: { media: "a1b2c3d4e5f6", alt: "a", src: "https://example.com/x.png" } })],
  ["a Schedule block at 24:00 (a clock is HH:MM, 00:00 to 23:59)", page({ type: "Schedule", props: { caption: "c", start: "07:00", end: "24:00", lanes: [{ name: "a", blocks: [] }] } })],
  ["a Schedule block time with seconds", page({ type: "Schedule", props: { caption: "c", start: "07:00", end: "14:00", lanes: [{ name: "a", blocks: [{ from: "08:00:00", to: "09:00", title: "x" }] }] } })],
  ["a Schedule without lanes", page({ type: "Schedule", props: { caption: "c", start: "07:00", end: "14:00", lanes: [] } })],
  ["a Schedule block with a className", page({ type: "Schedule", props: { caption: "c", start: "07:00", end: "14:00", lanes: [{ name: "a", blocks: [{ from: "08:00", to: "09:00", title: "x", className: "y" }] }] } })],
  ["a Contact phone that is a link", page({ type: "Contact", props: { items: [{ name: "a", phone: "tel:+85255501234" }] } })],
  ["a Contact phone with letters", page({ type: "Contact", props: { items: [{ name: "a", phone: "call me" }] } })],
  ["a Contact phone with a trailing newline", page({ type: "Contact", props: { items: [{ name: "a", phone: "5550 0101\n" }] } })],
  ["a Contact with an email address instead of a phone", page({ type: "Contact", props: { items: [{ name: "a", email: "a@example.com" }] } })],
  ["a Seating guest with an unknown status", page({ type: "Seating", props: { caption: "c", tables: [{ name: "T1", seats: [{ name: "a", status: "maybe" }] }] } })],
  ["a Seating table of 21", page({ type: "Seating", props: { caption: "c", capacity: 21, tables: [{ name: "T1", seats: [] }] } })],
  ["a Seating without tables", page({ type: "Seating", props: { caption: "c", tables: [] } })],
  ["a Progress with a negative value", page({ type: "Progress", props: { label: "a", value: -1, max: 10 } })],
  ["a Progress with a max of zero", page({ type: "Progress", props: { label: "a", value: 0, max: 0 } })],
  ["a Countdown date with a time zone", page({ type: "Countdown", props: { label: "a", date: "2027-06-12T00:00:00Z" } })],
  ["a Countdown time of 7pm", page({ type: "Countdown", props: { label: "a", date: "2027-06-12", time: "7pm" } })],
  ["a Timeline entry with an empty people list", page({ type: "Timeline", props: { items: [{ label: "a", title: "b", people: [] }] } })],
  ["a Checklist item with an unknown prop", page({ type: "Checklist", props: { items: [{ text: "a", done: true }] } })],
  ["a Table alignment of justify", page({ type: "Table", props: { columns: ["a"], rows: [], align: ["justify"] } })],
  ["a bad tone", page({ type: "Badge", props: { text: "x", tone: "neon" } })],
  ["five grid columns", page({ type: "Grid", props: { columns: 5 }, children: [] })],
  ["an unknown prop", page({ type: "Markdown", props: { text: "x", html: "<b>" } })],
  ["a prototype key", JSON.parse('{"version":1,"root":{"type":"Page","props":{"title":"T"},"children":[],"__proto__":{"x":1}}}')],
  ["nesting one level past the cap (9)", deep("Grid", 8)],
  ["a Card chain far past the cap (12)", deep("Card", 12)],
];
