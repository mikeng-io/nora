import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// The wedding demo's seating plan and contact list: axe-clean in both lightings, no
// horizontal scroll at the phone's width, and every phone is a real tel: link.
for (const theme of ["light", "dark"] as const) {
  test(`seating and contacts are accessible (${theme})`, async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page);
    await page.goto("/en/documents/spike?demo=wedding");
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    await expect(page.getByRole("heading", { level: 3 }).first()).toBeVisible();
    const axe = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious, JSON.stringify(serious.map((v) => [v.id, v.nodes.length]))).toEqual([]);
    errors.expectNone();
  });
}

test("a seating plan never scrolls the page sideways, and a phone is a tel link", async ({ page }) => {
  await signIn(page);
  await page.goto("/en/documents/spike?demo=wedding");
  await expect(page.getByRole("heading", { level: 3 }).first()).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const call = page.locator("a[href^='tel:']").first();
  await expect(call).toBeVisible();
  await expect(call).toHaveAttribute("href", /^tel:\+?\d{5,}$/);
  const box = await call.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.width).toBeGreaterThanOrEqual(44);
});
