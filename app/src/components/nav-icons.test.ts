import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MODULES } from "@/lib/modules";

// bottom-nav.tsx and nav-links.tsx both pull in "@/i18n/routing" (which
// wraps next-intl's app-router navigation, and in turn `next/navigation`)
// and "next-intl" itself. Neither resolves outside an actual Next.js
// request — Vitest's plain Vite resolver can't even locate `next/navigation`
// as a bare specifier. Stubbing these three collaborators lets the real
// component modules load and render in plain Node, so this test exercises
// the real ICONS map and the real render path instead of a copy of it.
vi.mock("@/i18n/routing", () => ({
  usePathname: () => "/status",
  Link: ({ href, className, children }: { href: string; className?: string; children?: ReactNode }) =>
    createElement("a", { href, className }, children),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/components/chat-sheet", () => ({
  ChatSheet: () => null,
}));

vi.mock("@/components/nav-sheet", () => ({
  NavSheet: () => null,
}));

describe("nav icon registries", () => {
  // Regression test for the bug where bottom-nav.tsx's ICONS record was
  // missing an "events" entry while nav-links.tsx's had one: MODULES.map()
  // is unconditional, so `ICONS[m.id]` resolved to `undefined` and
  // `<Icon .../>` threw "Element type is invalid" on every render of
  // BottomNav — which is mounted unconditionally in the portal layout
  // (`md:hidden` is CSS-only, not conditional rendering), so every
  // authenticated page crashed. `tsc` can't catch this (Record isn't
  // indexed-access-checked without noUncheckedIndexedAccess), and
  // `next build` never reaches it (prerendering redirects before an
  // authenticated layout mounts). Checked against the FULL MODULES list,
  // not just "live" entries, so a module flipping back to "live" later
  // can't reopen this gap by skipping its icon.
  it("every MODULES entry has a matching icon in both nav-links and bottom-nav", async () => {
    const { ICONS: navLinksIcons } = await import("./nav-links");
    const { ICONS: bottomNavIcons } = await import("./bottom-nav");

    for (const m of MODULES) {
      expect(navLinksIcons, `nav-links.tsx ICONS is missing "${m.id}"`).toHaveProperty(m.id);
      expect(bottomNavIcons, `bottom-nav.tsx ICONS is missing "${m.id}"`).toHaveProperty(m.id);
    }
  });

  // Belt-and-braces render smoke test: actually mount both nav components
  // (server-rendered, no jsdom needed) with the real MODULES array. A
  // missing icon key throws at render time regardless of what the
  // structural check above finds, so this catches the exact crash the
  // reviewer proved at runtime, not just a static shape mismatch.
  it("BottomNav renders without throwing for every module", async () => {
    const { BottomNav } = await import("./bottom-nav");
    expect(() => renderToStaticMarkup(createElement(BottomNav))).not.toThrow();
  });

  it("NavLinks renders without throwing for every module", async () => {
    const { NavLinks } = await import("./nav-links");
    expect(() => renderToStaticMarkup(createElement(NavLinks))).not.toThrow();
  });
});
