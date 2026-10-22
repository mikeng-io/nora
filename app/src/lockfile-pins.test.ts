// Aikido round 2 (npm lockfile pins): the copies that actually install must be the patched
// versions. Reads node_modules, so a lockfile that still resolves an old copy fails here.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const installed = (name: string): string =>
  JSON.parse(readFileSync(new URL(`../node_modules/${name}/package.json`, import.meta.url), "utf8")).version;

const atLeast = (version: string, minimum: string): boolean => {
  const have = version.split(".").map(Number);
  const want = minimum.split(".").map(Number);
  for (let i = 0; i < want.length; i++) {
    if ((have[i] ?? 0) !== want[i]) return (have[i] ?? 0) > want[i];
  }
  return true;
};

describe("npm lockfile pins (Aikido round 2)", () => {
  it.each([
    ["vitest", "5.0.3"], // Dependabot 126
    ["@vitest/mocker", "5.0.3"], // Dependabot 127
    ["dompurify", "3.4.17"], // 8764_80219 and low 8764_80284, 8764_80247
    ["inline-style-parser", "0.2.9"], // low 8764_80894
  ])("%s resolves to %s or newer", (name, minimum) => {
    const version = installed(name);
    expect(atLeast(version, minimum), `${name} installed ${version}, need >= ${minimum}`).toBe(true);
  });
});
