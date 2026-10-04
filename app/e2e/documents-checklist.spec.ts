import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// The wedding demo holds two Checklists. A tick is the reader's own: it lives in this
// browser's localStorage, survives a reload, and never blocks the page when storage is gone.
const URL = "/en/documents/spike?demo=wedding";

test("ticking counts, persists across a reload, and clears", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(URL);
  const list = page.getByRole("region", { name: "Before leaving the venue" });
  await expect(list.getByText("0 of 3 done").first()).toBeVisible();
  await expect(list.getByRole("button", { name: "Clear ticks" })).toHaveCount(0);

  await list.getByRole("checkbox", { name: /Leftover drinks/ }).check();
  await expect(list.getByText("1 of 3 done").first()).toBeVisible();
  await expect(list.getByRole("meter")).toHaveAttribute("aria-valuenow", "1");
  // The other list, with its own items, is untouched.
  await expect(page.getByRole("region", { name: "Before leaving the house" }).getByText("0 of 5 done").first()).toBeVisible();

  await page.reload();
  const again = page.getByRole("region", { name: "Before leaving the venue" });
  await expect(again.getByRole("checkbox", { name: /Leftover drinks/ })).toBeChecked();
  await expect(again.getByText("1 of 3 done").first()).toBeVisible();

  await again.getByRole("button", { name: "Clear ticks" }).click();
  await expect(again.getByText("0 of 3 done").first()).toBeVisible();
  await expect(again.getByRole("checkbox", { name: /Leftover drinks/ })).not.toBeChecked();
  errors.expectNone();
});

test("the keyboard ticks a row", async ({ page }) => {
  await signIn(page);
  await page.goto(URL);
  const box = page.getByRole("checkbox", { name: /Wedding shoes/ });
  await box.focus();
  await page.keyboard.press("Space");
  await expect(box).toBeChecked();
});

test("with storage blocked the page renders and ticking still works", async ({ page }) => {
  const errors = watchErrors(page);
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("blocked", "SecurityError"); } });
  });
  await signIn(page);
  await page.goto(URL);
  const box = page.getByRole("checkbox", { name: /Leftover drinks/ });
  await box.check();
  await expect(box).toBeChecked();
  await expect(page.getByText("1 of 3 done").first()).toBeVisible();
  errors.expectNone();
});

for (const theme of ["light", "dark"] as const) {
  test(`checklist, timeline, table and donut are axe-clean (${theme})`, async ({ page }) => {
    await signIn(page);
    await page.goto(URL);
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    await page.getByRole("checkbox", { name: /Leftover drinks/ }).check();
    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious, JSON.stringify(serious.map((v) => [v.id, v.nodes.length]))).toEqual([]);
  });
}

test("nothing overflows sideways at 320px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await signIn(page);
  await page.goto(URL);
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
});
