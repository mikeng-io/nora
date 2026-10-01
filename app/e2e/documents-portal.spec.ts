import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// The real pages (ADR 0020): the list, one document drawn from its stored spec, and the
// picture route. The mock API (dev/mock-nora.mjs) holds five documents: four Miles can
// read (one revised, one whose spec this build refuses, one with its own link handle) and
// one only Mei can.
const LISBON = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01";
const NEWER = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e03";
const MEIS = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e04";
const PICTURE = "b".repeat(64);
// A link carries a 12-hex handle, never the UUID (a UUID in a reply is blocked at egress).
const PACKING = "9e1d2c3b-4a5f-4617-8899-aabbccddeeff";
const PACKING_HANDLE = "9e1d2c3b4a5f";
const SHARED_HANDLE = "5b6f0c3a2d41"; // the prefix all four other fixtures share

test("the list shows what Miles may read, newest first, and opens a document", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/en/documents");
  await expect(page.getByRole("heading", { level: 1, name: "Documents" })).toBeVisible();
  const rows = page.getByRole("list", { name: "Documents" }).getByRole("link");
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText("Lisbon in December");
  await expect(rows.nth(0)).toContainText("Ken · Signal");
  await expect(rows.nth(0)).toContainText("Revision 3");
  await expect(rows.nth(1)).toContainText("Kitchen rota");
  // A document only Mei can read is not in Miles's list.
  await expect(page.getByText("Mei's surprise")).toHaveCount(0);
  await rows.nth(0).click();
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}$`));
  await expect(page.getByRole("heading", { level: 1, name: "Lisbon in December" })).toBeVisible();
  errors.expectNone();
});

test("the rail has a Documents link on a desktop", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === "phone", "the phone's bottom bar holds four places");
  await signIn(page);
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Documents" }).click();
  await expect(page).toHaveURL(/\/documents$/);
});

for (const theme of ["light", "dark"] as const) {
  test(`a document renders with its picture and no serious a11y violation (${theme})`, async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page);
    await page.goto(`/en/documents/${LISBON}`);
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByTestId("document-meta")).toContainText("Made by Ken in Signal · revision 3");
    const img = page.getByRole("img", { name: "The hotel door" });
    await expect(img).toBeVisible();
    // The bytes arrived through the picture route, not a broken-image icon.
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
    await expect(img).toHaveAttribute("src", `/api/v1/documents/${LISBON}/media/${PICTURE}`);
    const axe = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious, JSON.stringify(serious.map((v) => [v.id, v.nodes.length]))).toEqual([]);
    errors.expectNone();
  });
}

test("a stored spec this build refuses is said so, not half drawn", async ({ page }) => {
  await signIn(page);
  await page.goto(`/en/documents/${NEWER}`);
  // (Next's own route announcer is also a role=alert, so pick ours by its words.)
  await expect(page.getByRole("alert").filter({ hasText: "cannot be shown" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
});

test("a document Miles may not read looks exactly like one that does not exist", async ({ page }) => {
  await signIn(page);
  const texts: string[] = [];
  // A handle that matches nothing, and one that matches SEVERAL documents Miles may read,
  // look exactly like an unreadable or unknown id: no oracle for which, and none picked.
  for (const id of [MEIS, "00000000-0000-4000-8000-000000000000", "not-a-document", "ffffffffffff", SHARED_HANDLE]) {
    await page.goto(`/en/documents/${id}`);
    await expect(page.getByText("Not one of your documents")).toBeVisible();
    texts.push(await page.getByRole("main").innerText());
  }
  expect(new Set(texts).size).toBe(1);
});

test("a link's handle opens the one document Miles may read under it", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(`/en/documents/${PACKING_HANDLE}`);
  await expect(page.getByRole("heading", { level: 1, name: "Saturday packing list" })).toBeVisible();
  await expect(page.getByTestId("document-meta")).toContainText("Made by Miles in Signal");
  // The full id still opens it too, and the list links by id.
  await page.goto(`/en/documents/${PACKING}`);
  await expect(page.getByRole("heading", { level: 1, name: "Saturday packing list" })).toBeVisible();
  errors.expectNone();
});

test("the picture route serves a raster with the headers that keep it inert, and refuses the rest", async ({
  page,
  playwright,
  baseURL,
}) => {
  await signIn(page);
  const ok = await page.request.get(`/api/v1/documents/${LISBON}/media/${PICTURE}`);
  expect(ok.status()).toBe(200);
  expect(ok.headers()["content-type"]).toBe("image/png");
  expect(ok.headers()["x-content-type-options"]).toBe("nosniff");
  expect(ok.headers()["content-security-policy"]).toContain("sandbox");
  expect(ok.headers()["cache-control"]).toBe("private, no-store");
  // Not named by the document, not readable by Miles, not an id, not a sha: all 404.
  for (const url of [
    `/api/v1/documents/${LISBON}/media/${"c".repeat(64)}`,
    `/api/v1/documents/${MEIS}/media/${PICTURE}`,
    `/api/v1/documents/not-a-uuid/media/${PICTURE}`,
    `/api/v1/documents/${LISBON}/media/${PICTURE.slice(0, 12)}`,
  ]) {
    expect((await page.request.get(url)).status(), url).toBe(404);
  }
  // Signed out, there is no picture to ask for.
  const anon = await playwright.request.newContext({ baseURL });
  expect((await anon.get(`/api/v1/documents/${LISBON}/media/${PICTURE}`)).status()).toBe(401);
  await anon.dispose();
});

test("the list has no serious a11y violation, in both lightings", async ({ page }) => {
  await signIn(page);
  for (const theme of ["light", "dark"] as const) {
    await page.goto("/en/documents");
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    await expect(page.getByRole("list", { name: "Documents" })).toBeVisible();
    const axe = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious, `${theme}: ${JSON.stringify(serious.map((v) => [v.id, v.nodes.length]))}`).toEqual([]);
  }
});
