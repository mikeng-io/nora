import { describe, expect, it } from "vitest";
import { TITLE_SUFFIX, documentTabTitle, shouldReassertTitle, shouldRestoreTitle } from "./title";

describe("a document's browser tab title (b)", () => {
  it("is the document's own title with the site suffix, not the section's name", () => {
    expect(documentTabTitle("Lisbon in December", "Documents")).toBe("Lisbon in December · Nora");
    expect(documentTabTitle("Lisbon in December", "Documents")).not.toContain("Documents");
  });

  it("tells two documents apart", () => {
    expect(documentTabTitle("Packing list", "Documents")).not.toBe(documentTabTitle("Kitchen rota", "Documents"));
  });

  it("says the section's name until the title is known, or when it is blank", () => {
    expect(documentTabTitle(undefined, "Documents")).toBe(`Documents${TITLE_SUFFIX}`);
    expect(documentTabTitle(null, "文件")).toBe("文件 · Nora");
    expect(documentTabTitle("   ", "Documents")).toBe("Documents · Nora");
  });

  it("collapses whitespace a title may carry, so the tab is one tidy line", () => {
    expect(documentTabTitle("  Trip \n  plan ", "Documents")).toBe("Trip plan · Nora");
  });

  it("uses the same suffix as the layout's title template", async () => {
    const { readFileSync } = await import("node:fs");
    const layout = readFileSync(new URL("../../app/[locale]/layout.tsx", import.meta.url), "utf-8");
    expect(layout).toContain(`template: \`%s${TITLE_SUFFIX}\``);
  });
});

describe("who may write the tab title (b)", () => {
  const mine = "Lisbon in December · Nora";
  const here = "/en/documents/5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01";

  it("takes the title back when Next writes the route's own over it, even from an empty tab", () => {
    // Entering a cached document: the page's effect runs, then Next writes "Documents · Nora"
    // (and the tab can read "" while it swaps its <title>).
    expect(shouldReassertTitle("Documents · Nora", mine, here, here)).toBe(true);
    expect(shouldReassertTitle("", mine, here, here)).toBe(true);
  });

  it("leaves the title alone once the address has changed, so the next page's own is never fought", () => {
    expect(shouldReassertTitle("Plans · Nora", mine, here, "/en/plans")).toBe(false);
    expect(shouldReassertTitle(mine, mine, here, here)).toBe(false);
  });

  it("puts the replaced title back on leaving only while the tab still says its own", () => {
    expect(shouldRestoreTitle(mine, mine)).toBe(true);
    // Another page already set its title: it is not overwritten with a stale one.
    expect(shouldRestoreTitle("Plans · Nora", mine)).toBe(false);
  });
});
