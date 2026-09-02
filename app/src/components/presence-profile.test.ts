import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { createTranslator } from "use-intl";
import en from "../../messages/en.json";
import zhHant from "../../messages/zh-Hant.json";

// Same pattern as nav-icons.test.ts: next-intl and the fetch layer don't
// resolve/Run outside a Next.js request, so they're stubbed; the component
// under test is the real module. The fetchers return never-settling
// promises — the drawer must render its loading skeleton without data (and
// without throwing), which is the state this smoke test pins.
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/presence-schema", () => ({
  fetchPresenceAnalyticsPlaces: () => new Promise(() => {}),
  fetchPresenceRhythm: () => new Promise(() => {}),
  fetchPresenceRoutes: () => new Promise(() => {}),
}));

import { PresenceProfile } from "./presence-profile";

describe("PresenceProfile", () => {
  it("renders the dialog with header, live state and all three sections while data loads", () => {
    const html = renderToStaticMarkup(
      createElement(PresenceProfile, {
        entry: {
          personId: "miles",
          displayName: "Miles",
          current: {
            lat: 22.3, lng: 114.2, ts: 1, ageSeconds: 30,
            wireState: "live" as const, isDriving: true, address: "Nathan Road",
          },
        },
        onClose: () => {},
      }),
    );
    expect(html).toContain('role="dialog"');
    expect(html).toContain("Miles");
    expect(html).toContain("Nathan Road");
    for (const section of ["analytics.tabs.places", "analytics.tabs.rhythm", "analytics.tabs.routes"]) {
      expect(html).toContain(section);
    }
  });
});

// The drawer's new map.profile.* messages carry ICU plurals/placeholders —
// a malformed one throws AT RENDER TIME in the browser, which no typecheck
// catches. Format each key in both locales here instead.
describe("map.profile messages", () => {
  for (const [locale, messages] of Object.entries({ en, "zh-Hant": zhHant })) {
    it(`formats every profile key in ${locale}`, () => {
      const t = createTranslator({ locale, messages, namespace: "map" });
      expect(t("profile.open")).toBeTruthy();
      expect(t("profile.close")).toBeTruthy();
      expect(t("profile.peak")).toBeTruthy();
      expect(t("profile.trips", { count: 1 })).toContain("1");
      expect(t("profile.trips", { count: 5 })).toContain("5");
      expect(t("profile.lastSeen", { date: "Sep 2" })).toContain("Sep 2");
      expect(t("profile.unmatched", { count: 3 })).toContain("3");
      expect(t("profile.window", { days: 30 })).toContain("30");
    });
  }
});
