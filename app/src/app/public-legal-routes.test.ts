import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// The sign-in gate is the (portal) layout (src/proxy.ts says so). So a route
// is public exactly when nothing above it is that layout and nothing in its
// own files asks for a session. Google's consent screen needs /privacy and
// /terms readable by anyone, in both locales.
const root = new URL("./[locale]/", import.meta.url);
const read = (rel: string) => readFileSync(new URL(rel, root), "utf-8");

describe.each(["privacy", "terms"])("the public /%s page", (route) => {
  it("lives outside the (portal) group, which is the sign-in gate", () => {
    expect(existsSync(new URL(`${route}/page.tsx`, root))).toBe(true);
    expect(existsSync(new URL(`(portal)/${route}`, root))).toBe(false);
  });

  it("never asks for a session or redirects", () => {
    const files = [`${route}/page.tsx`, "../../components/legal/legal-page.tsx"];
    for (const f of files) {
      const src = read(f);
      expect(src, f).not.toMatch(/checkSession|@\/server\/|redirect\(|next-auth/);
    }
  });

  it("is reached by the locale middleware and not excluded from it", () => {
    const proxy = readFileSync(new URL("../proxy.ts", import.meta.url), "utf-8");
    // The matcher excludes only api, _next, _vercel and file extensions.
    expect(proxy).toContain('"/((?!api|_next|_vercel|.*\\\\..*).*)"');
    expect(proxy).not.toContain(route);
  });
});

it("the (portal) layout is the gate, so the premise of the test holds", () => {
  expect(read("(portal)/layout.tsx")).toContain('redirect(`/${locale}/login`)');
});
