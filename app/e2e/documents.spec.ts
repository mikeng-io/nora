import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// Every demo document renders through the catalog's registry on the portal
// theme, with no uncaught error and no serious accessibility violation, in both
// lightings, on a phone and on a desktop. W2 fixture route; W5 replaces it.
const DEMOS = ["trip", "report", "comparison", "evening", "runbook"];

for (const theme of ["light", "dark"] as const) {
  for (const demo of DEMOS) {
    test(`demo ${demo} renders (${theme})`, async ({ page }) => {
      const errors = watchErrors(page);
      await signIn(page);
      await page.goto(`/en/documents/spike?demo=${demo}`);
      await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const axe = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious, JSON.stringify(serious.map((v) => [v.id, v.nodes.length]))).toEqual([]);
      errors.expectNone();
    });
  }
}

test("hostile markdown never reaches the page", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/en/documents/spike?demo=sink");
  await expect(page.getByRole("heading", { level: 1, name: "Kitchen sink" })).toBeVisible();
  // Raw HTML is text, an image is refused, a javascript: link has no href.
  await expect(page.locator("[data-testid=spike] script")).toHaveCount(0);
  await expect(page.locator("[data-testid=spike] img")).toHaveCount(0);
  await expect(page.locator("[data-testid=spike] a[href^='javascript']")).toHaveCount(0);
  await expect(page.getByText("<script>alert(1)</script>")).toBeVisible();
  await expect(page.getByRole("link", { name: "link", exact: true })).toHaveAttribute("rel", /noopener.*nofollow/);
  errors.expectNone();
});

test("tabs show one panel at a time and switch on click", async ({ page }) => {
  await signIn(page);
  await page.goto("/en/documents/spike?demo=runbook");
  await expect(page.getByText("Wait about")).toBeVisible();
  await expect(page.getByText("Open the game's page")).toBeHidden();
  await page.getByRole("tab", { name: "From the portal" }).click();
  await expect(page.getByText("Open the game's page")).toBeVisible();
  await expect(page.getByText("Wait about")).toBeHidden();
});

test("an accordion opens and closes", async ({ page }) => {
  await signIn(page);
  await page.goto("/en/documents/spike?demo=trip");
  const item = page.getByText("Getting around", { exact: true });
  await expect(page.getByText("Buy a")).toBeHidden();
  await item.click();
  await expect(page.getByText("Buy a")).toBeVisible();
});

test("a chart names its claim and offers its data as a table", async ({ page }) => {
  await signIn(page);
  await page.goto("/en/documents/spike?demo=report");
  await expect(page.getByText("busiest on the two game nights")).toHaveCount(1);
  await page.getByText("Show the data").click();
  await expect(page.locator("figure").getByRole("cell", { name: "Wed", exact: true })).toBeVisible();
});

test("a diagram is drawn by mermaid in strict mode", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/en/documents/spike?demo=trip");
  const fig = page.getByRole("img", { name: /about 40 minutes by metro/ });
  await expect(fig.locator("svg")).toBeVisible({ timeout: 15_000 });
  await expect(fig.locator("script, foreignObject, [onclick]")).toHaveCount(0);
  errors.expectNone();
});

test("hostile mermaid source runs nothing and fails in the open", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => { dialogs.push(d.message()); void d.dismiss(); });
  await signIn(page);
  await page.goto("/en/documents/spike?demo=mermaid-sink");
  // Either it draws (sanitised) or it says it could not; never a blank figure.
  const fig = page.locator("[data-testid=spike] figure");
  await expect(fig.locator("svg, [role=note]").first()).toBeVisible({ timeout: 15_000 });
  await expect(fig.locator("a[href^='javascript'], [onclick], img, script")).toHaveCount(0);
  await page.waitForTimeout(500);
  expect(dialogs).toEqual([]);
});

test("an image with no stored document shows its description, not a broken request", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/en/documents/spike?demo=trip");
  await expect(page.getByRole("img", { name: /green door between two shops/ })).toBeVisible();
  await expect(page.locator("[data-testid=spike] img")).toHaveCount(0);
  errors.expectNone();
});

test("a map is drawn, framed on its places, and names them in text", async ({ page }) => {
  await signIn(page);
  await page.goto("/en/documents/spike?demo=trip");
  // The map's own container, not its canvas: MapLibre draws a default 300px
  // canvas into a zero-height container, so the canvas and the pins look present
  // while nothing is visible. Two earlier versions of this test passed on that.
  const map = page.locator(".maplibregl-map").first();
  await map.scrollIntoViewIfNeeded();
  await expect.poll(async () => (await map.boundingBox())?.height ?? 0).toBeGreaterThan(250);
  await expect(page.locator(".maplibregl-marker")).toHaveCount(4);
  await expect(page.getByRole("list").filter({ hasText: "Cais do Sodré" }).first()).toBeVisible();
});
