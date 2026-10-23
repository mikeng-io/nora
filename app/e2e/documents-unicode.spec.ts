import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

for (const length of [4000, 4001]) {
  test(`a stored ${length}-code-point Mermaid is ${length === 4000 ? "drawn" : "refused"}`, async ({ page }) => {
    const prefix = 'flowchart LR\nA["';
    const suffix = '"]';
    const source = prefix + "😀".repeat(length - prefix.length - suffix.length) + suffix;
    await signIn(page);
    // Substitute only a synthetic stored spec; rendering still runs through
    // the real document page, browser schema and Mermaid SVG renderer.
    await page.route("**/api/v1/documents/unicode-boundary", (route) => route.fulfill({
      json: {
        id: "unicode-boundary", title: "Unicode boundary", revision: 1, platform: "signal",
        createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
        createdBy: { personId: "person-a", name: "Example" }, media: [],
        document: { version: 1, root: { type: "Page", props: { title: "Unicode boundary" }, children: [
          { type: "Mermaid", props: { source, caption: "A boundary diagram" } },
        ] } },
      },
    }));
    await page.goto("/en/documents/unicode-boundary");
    if (length === 4000) {
      await expect(page.getByRole("img", { name: "A boundary diagram" }).locator("svg"))
        .toBeVisible({ timeout: 15_000 });
    } else {
      await expect(page.getByText("This document cannot be shown")).toBeVisible();
      await expect(page.locator("figure svg")).toHaveCount(0);
    }
  });
}
