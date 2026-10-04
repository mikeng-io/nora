import { expect, test } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// The wedding demo's Schedule, Timeline, Countdown and Progress. The axe pass over the
// whole page lives in documents-checklist.spec.ts; these say what each block does.
const URL = "/en/documents/spike?demo=wedding";
const CAPTION = "Who is where, from the first bus to the last photo";

test("a schedule is a focusable scroll grid from sm up and a plain list on a phone", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(URL);
  const wide = (page.viewportSize()?.width ?? 0) >= 640;
  const grid = page.getByRole("region", { name: CAPTION });
  if (wide) {
    await expect(grid).toBeVisible();
    await grid.focus();
    await expect(grid).toBeFocused();
    // Seven hours on the axis is wider than the page's column, so the region scrolls, not the page.
    const scrolls = await grid.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(scrolls).toBe(true);
    await expect(grid.getByRole("group", { name: "Treasurer" })).toBeVisible();
  } else {
    await expect(grid).toBeHidden();
    await expect(page.getByText("Rings and IDs").first()).toBeVisible();
  }
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
  errors.expectNone();
});

test("a timeline entry draws its list as a list and names who it falls to", async ({ page }) => {
  await signIn(page);
  await page.goto(URL);
  const registrar = page.getByRole("listitem").filter({ hasText: "Registrar" }).filter({ hasText: "Both IDs" }).first();
  await expect(registrar.getByRole("listitem").filter({ hasText: /Both IDs|The two rings|Two witnesses/ })).toHaveCount(3);
  await expect(registrar.getByText("Treasurer")).toBeVisible();
});

test("the countdown fills in after mount and the progress bar is a meter", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(URL);
  const figure = page.locator("[data-countdown]");
  await expect(figure).not.toHaveAttribute("data-countdown", "pending");
  await expect(figure).toHaveText(/\S/);
  const meter = page.getByRole("meter", { name: "Replies in" });
  await expect(meter).toHaveAttribute("aria-valuenow", "74");
  await expect(meter).toHaveAttribute("aria-valuemax", "90");
  errors.expectNone();
});
