import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import zh from "../../messages/zh-Hant.json";

// Key parity and placeholder parity between the two locales. The Python
// guard (tests/architecture/test_portal_locales_make_the_same_claims.py)
// checks the same files for claims; this checks what would render wrong:
// a key missing in one language shows as its raw path, and a placeholder
// missing in one leaves a sentence without its number.
type Tree = { [k: string]: string | Tree };

function flatten(node: Tree, prefix = ""): Record<string, string> {
  return Object.entries(node).reduce<Record<string, string>>((acc, [k, v]) => {
    const path = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "string") acc[path] = v;
    else Object.assign(acc, flatten(v, path));
    return acc;
  }, {});
}

const placeholders = (s: string) =>
  [...s.matchAll(/\{(\w+)(?:,|\})/g)].map((m) => m[1]).filter((v, i, a) => a.indexOf(v) === i).sort();

describe("messages", () => {
  const a = flatten(en as Tree);
  const b = flatten(zh as Tree);

  it("carry the same keys in both languages", () => {
    expect(Object.keys(b).sort()).toEqual(Object.keys(a).sort());
  });

  // The document renderer's own words (aria labels, fallbacks, error text) are owned by
  // `documents.renderer`, in both languages (h). A string left in the source is English for
  // everyone; a key missing from one file renders as its raw path in that language.
  describe("the documents renderer namespace", () => {
    const ns = (flat: Record<string, string>) =>
      Object.keys(flat).filter((k) => k.startsWith("documents.renderer."));

    it("has the same keys in both languages, and is not empty", () => {
      expect(ns(a).length).toBeGreaterThan(5);
      expect(ns(b).sort()).toEqual(ns(a).sort());
    });

    it("has a Chinese value that is not just the English one", () => {
      const same = ns(a).filter((k) => a[k] === b[k]);
      expect(same).toEqual([]);
    });

    it("is every key the renderer's source asks for, and the source keeps no English string of its own", async () => {
      const { readFileSync } = await import("node:fs");
      const dir = new URL("../components/documents/", import.meta.url);
      const wanted = new Set<string>();
      for (const file of ["registry.tsx", "blocks.tsx"]) {
        const source = readFileSync(new URL(file, dir), "utf-8");
        for (const m of source.matchAll(/\bt\("([A-Za-z]+)"/g)) wanted.add(`documents.renderer.${m[1]}`);
        // The words the renderer used to hard-code.
        for (const english of [
          "Show the data",
          "This diagram could not be drawn",
          "Diagram source",
          "This picture is no longer available",
          "This document cannot be shown",
          "· read ",
        ]) {
          expect(source, `${file} still says "${english}"`).not.toContain(english);
        }
      }
      expect(wanted.size).toBeGreaterThan(5);
      for (const key of wanted) {
        expect(a, `en lacks ${key}`).toHaveProperty([key]);
        expect(b, `zh-Hant lacks ${key}`).toHaveProperty([key]);
      }
      // And nothing in the namespace is unused: a key the source never asks for is drift.
      expect([...wanted].sort()).toEqual(ns(a).sort());
    });
  });

  it("use the same placeholders for each key", () => {
    const drift = Object.keys(a)
      .filter((k) => k in b && placeholders(a[k]).join() !== placeholders(b[k]).join())
      .map((k) => `${k}: en {${placeholders(a[k])}} zh {${placeholders(b[k])}}`);
    expect(drift).toEqual([]);
  });
});
