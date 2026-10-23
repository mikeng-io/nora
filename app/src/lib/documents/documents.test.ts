import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020";
import { describe, expect, it } from "vitest";
import { COMPONENT_NAMES, MAX_DEPTH, MAX_NODES, documentSchema, parseDocument } from "./catalog";
import { INVALID, VALID } from "./corpus";
import { DEMO_NAMES, DEMOS } from "./demos";
import { emitCorpus, emitPrompt, emitSchema } from "./emit";

const committed = (name: string) =>
  readFileSync(fileURLToPath(new URL(`../../../../nora/documents/${name}`, import.meta.url)), "utf8");

const ajv = new Ajv2020({ strict: false });
const validateJson = ajv.compile(JSON.parse(emitSchema()));

describe("the emitted files match the catalog", () => {
  // A stale file means Nora's checker and the skill describe a different
  // catalog than the one the portal renders. Fix: npm run documents:emit.
  it("document.schema.json", () => expect(committed("document.schema.json")).toBe(emitSchema()));
  it("catalog-prompt.md", () => expect(committed("catalog-prompt.md")).toBe(emitPrompt()));
  it("corpus.json", () => expect(committed("corpus.json")).toBe(emitCorpus({ valid: VALID, invalid: INVALID })));
});

describe("the prompt fragment", () => {
  it("names every component in the catalog", () => {
    const prompt = emitPrompt();
    for (const name of COMPONENT_NAMES) expect(prompt).toContain(`**${name}**`);
  });
  it("does not promise what the schema refuses", () => {
    // It said "no URL except in Sources" while Markdown links were accepted.
    expect(emitPrompt()).not.toContain("no URL except");
  });
});

describe("the valid corpus", () => {
  for (const [label, doc] of Object.entries(VALID)) {
    it(`${label}: Zod accepts it`, () => {
      const r = parseDocument(doc);
      expect(r.ok, r.ok ? "" : r.reason).toBe(true);
    });
    it(`${label}: the JSON Schema accepts it`, () => {
      expect(validateJson(doc), JSON.stringify(validateJson.errors?.slice(0, 2))).toBe(true);
    });
  }
});

describe("what a document may not contain", () => {
  for (const [label, doc] of INVALID) {
    it(`Zod refuses ${label}`, () => expect(parseDocument(doc).ok).toBe(false));
    it(`the JSON Schema refuses ${label}`, () => expect(validateJson(doc)).toBe(false));
  }
});

describe("two validators, one verdict", () => {
  // The Python checker has only the JSON Schema. Wherever the two could differ
  // (defaults, anchors, unions, depth), this pins the same answer on both.
  it("agree on every corpus document", () => {
    const all: Array<[string, unknown]> = [...Object.entries(VALID), ...INVALID];
    const split = all.filter(([, doc]) => parseDocument(doc).ok !== validateJson(doc));
    expect(split.map(([label]) => label)).toEqual([]);
  });

  it.each([3999, 4000, 4001])("counts %i Mermaid Unicode code points the same way", (length) => {
    const prefix = 'flowchart LR\nA["';
    const suffix = '"]';
    const source = prefix + "😀".repeat(length - prefix.length - suffix.length) + suffix;
    const doc = { version: 1, root: { type: "Page", props: { title: "T" }, children: [
      { type: "Mermaid", props: { source, caption: "A boundary diagram" } },
    ] } };
    expect(parseDocument(doc).ok).toBe(length <= 4000);
    expect(validateJson(doc)).toBe(length <= 4000);
  });
});

describe("parseDocument never throws and never recurses on hostile depth", () => {
  const chain = (levels: number) => {
    let node: unknown = { type: "Markdown", props: { text: "x" } };
    for (let i = 0; i < levels; i++) node = { type: "Card", props: {}, children: [node] };
    return { version: 1, root: { type: "Page", props: { title: "T" }, children: [node] } };
  };
  it("refuses a 1,200-deep chain with a reason instead of a RangeError", () => {
    const r = parseDocument(chain(1200));
    expect(r).toEqual({ ok: false, reason: `nested deeper than ${MAX_DEPTH}` });
  });
  it("refuses more nodes than the cap", () => {
    const leaf = { type: "Badge", props: { text: "x" } };
    const wide = { version: 1, root: { type: "Page", props: { title: "T" }, children: Array.from({ length: 200 }, () => leaf) } };
    const two = { ...wide, root: { ...wide.root, children: [{ type: "Grid", props: {}, children: wide.root.children }, { type: "Grid", props: {}, children: wide.root.children }, { type: "Grid", props: {}, children: wide.root.children }] } };
    const r = parseDocument(two);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.reason).toContain(`${MAX_NODES}`);
  });
  it("reports a non-document without throwing", () => {
    for (const v of [null, undefined, 1, "x", [], {}, { version: 1 }, { version: 1, root: null }]) {
      expect(parseDocument(v).ok).toBe(false);
    }
  });
  it("applies the defaults the renderer relies on", () => {
    const r = parseDocument({ version: 1, root: { type: "Page", props: { title: "T" }, children: [{ type: "Badge", props: { text: "x" } }] } });
    expect(r.ok && (r.doc.root.children[0] as { props: { tone?: string } }).props.tone).toBe("neutral");
  });
});

describe("the demo documents", () => {
  // They are what the skill shows the model and what the viewer is tested on,
  // so one that stops validating is a bug in both.
  it("are registered", () => expect([...DEMO_NAMES].sort()).toEqual(["comparison", "evening", "report", "runbook", "trip", "wedding"]));
  for (const name of DEMO_NAMES) {
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
  it("schema and the strict object both stay closed on the Page", () => {
    expect(documentSchema.safeParse({ ...DEMOS.trip, extra: 1 }).success).toBe(false);
  });
});

describe("mermaid is pinned, because the source allowlist is written against its grammar", () => {
  // Every mermaid release can add a statement or a separator the allowlist has
  // not seen (two review passes found four such channels). A caret or a tilde
  // would let an install pick one up unreviewed; the hostile-source e2e and the
  // corpus are what to re-run on each deliberate upgrade.
  it("is an exact version in package.json", () => {
    const pkg = JSON.parse(readFileSync(fileURLToPath(new URL("../../../package.json", import.meta.url)), "utf8"));
    expect(pkg.dependencies.mermaid).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
