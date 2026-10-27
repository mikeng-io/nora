import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// Every page renders its real content from the mock, with no uncaught error
// and no serious accessibility violation, on a phone and on a desktop.
const PAGES: Array<{ path: string; heading: RegExp }> = [
  { path: "/en/today", heading: /Morning|Afternoon|Evening|Still up/ },
  { path: "/en/map", heading: /Where everyone is/ },
  { path: "/en/people", heading: /^People$/ },
  { path: "/en/people/ken", heading: /^Ken$/ },
  { path: "/en/people/ken?tab=rhythm", heading: /^Ken$/ },
  { path: "/en/people/miles?tab=memory", heading: /^Miles$/ },
  { path: "/en/plans", heading: /^Plans$/ },
  { path: "/en/games", heading: /^Games$/ },
  { path: "/en/games/abiotic", heading: /Abiotic Factor/ },
  { path: "/en/games/abiotic?tab=insights", heading: /Abiotic Factor/ },
  { path: "/en/games/bigwalk?tab=world", heading: /The Big Walk/ },
  { path: "/en/downloads", heading: /^Downloads$/ },
  { path: "/en/nora", heading: /Nora/ },
  { path: "/en/nora/memory", heading: /memory/i },
  { path: "/en/nora/doctrine", heading: /.+/ },
  { path: "/en/nora/abilities", heading: /.+/ },
];

test.describe("signed in", () => {
  test.beforeEach(async ({ page }) => signIn(page));

  for (const { path, heading } of PAGES) {
    test(`renders ${path}`, async ({ page }) => {
      const errors = watchErrors(page);
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toHaveText(heading);
      // Wait for the reads to settle: no skeleton left standing.
      await page.waitForLoadState("networkidle");
      const axe = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        // MapLibre's canvas and its attribution control are the library's.
        .exclude(".maplibregl-ctrl")
        .analyze();
      const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
      errors.expectNone();
    });
  }

  test("the command palette finds a person and opens them", async ({ page, isMobile }) => {
    await page.goto("/en/today");
    if (isMobile) await page.getByRole("button", { name: "Search" }).click();
    else await page.keyboard.press("ControlOrMeta+k");
    await page.getByPlaceholder(/Search people/).fill("ken");
    // The roster may still be arriving; the person must be listed first.
    await expect(page.getByRole("option", { name: /Ken/ }).first()).toHaveAttribute("data-selected", "true");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/people\/ken$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Ken");
  });

  test("an unknown plan says it isn't yours rather than failing", async ({ page }) => {
    await page.goto("/en/plans/not-a-real-plan");
    await expect(page.getByText("Not one of your plans")).toBeVisible();
  });

  test("the map selects a person from the list and keeps it in the URL", async ({ page }) => {
    await page.goto("/en/map");
    await page.getByRole("button", { name: /^Ken/ }).first().click();
    await expect(page).toHaveURL(/person=ken/);
    await expect(page.getByRole("heading", { name: "Ken", level: 2 })).toBeVisible();
  });
});

test("the portal refuses a signed-out visitor and sends them to sign in", async ({ page }) => {
  await page.goto("/en/today");
  await expect(page).toHaveURL(/\/en\/login/);
  const res = await page.request.get("/api/v1/status");
  expect(res.status()).toBe(401);
});

test("the API proxy never serves another person's memory", async ({ page }) => {
  await signIn(page, "Ken");
  const res = await page.request.get("/api/v1/memory?personId=miles");
  expect(res.ok()).toBe(true);
  const body = (await res.json()) as Array<{ person_id: string }>;
  expect(body.map((e) => e.person_id)).toEqual(["ken"]);
});
