import { describe, expect, it } from "vitest";
import { TITLE_SUFFIX, documentTabTitle } from "./title";

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
