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

  it("use the same placeholders for each key", () => {
    const drift = Object.keys(a)
      .filter((k) => k in b && placeholders(a[k]).join() !== placeholders(b[k]).join())
      .map((k) => `${k}: en {${placeholders(a[k])}} zh {${placeholders(b[k])}}`);
    expect(drift).toEqual([]);
  });
});
