import { expect, test, type Page } from "@playwright/test";
import { watchErrors } from "./helpers";

// Sign-in with a one-time code (DEC-287), against the mock: the identifier is
// Miles's invented number (+852 0000 0001, dev/identity.dev.toml) and the code
// is always 123456 (dev/mock-nora.mjs).
//
// The portal lets one visitor ask for 3 codes in 15 minutes, so every test
// is a different visitor: its own forwarded address.
let visitor = 0;
async function asNewVisitor(page: Page) {
  visitor += 1;
  await page.setExtraHTTPHeaders({ "x-forwarded-for": `198.51.100.${visitor}-${Date.now()}` });
}

test("a code on WhatsApp signs in as the same person Discord would", async ({ page }) => {
  const errors = watchErrors(page);
  await asNewVisitor(page);
  await page.goto("/en/login");
  await page.getByText("WhatsApp", { exact: true }).click();
  await page.getByLabel("Your phone number").fill("+852 0000 0001");
  await page.getByRole("button", { name: "Send me a code" }).click();

  await page.getByLabel("The code").fill("654321");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("That code didn't work", { exact: false })).toBeVisible();

  await page.getByLabel("The code").fill("123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(/\/today$/);
  await expect(page.getByRole("heading", { name: /Miles/ })).toBeVisible();
  // Attached, not visible: on a phone the account chip lives in the hidden rail.
  await expect(page.getByText("Signed in with a code").first()).toBeAttached();
  errors.expectNone();
});

test("a number nobody knows is answered the same way, and no code works", async ({ page }) => {
  await asNewVisitor(page);
  await page.goto("/en/login");
  await page.getByLabel("Your phone number").fill("+85200000000");
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByText(/If \+85200000000 is on the list/)).toBeVisible();
  await page.getByLabel("The code").fill("123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("That code didn't work", { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("one visitor asking for a fourth code in a few minutes is told to wait", async ({ page }) => {
  await asNewVisitor(page);
  await page.goto("/en/login");
  for (let i = 0; i < 3; i += 1) {
    await page.getByLabel("Your phone number").fill("+85200000000");
    await page.getByRole("button", { name: "Send me a code" }).click();
    await page.getByRole("button", { name: "Use a different number" }).click();
  }
  await page.getByLabel("Your phone number").fill("+85200000000");
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByText("That's a few codes in a short time", { exact: false })).toBeVisible();
});

test("a local number is joined to Hong Kong, the default country code", async ({ page }) => {
  await asNewVisitor(page);
  await page.goto("/en/login");
  await expect(page.getByLabel("Country or region code")).toHaveValue("HK");
  await page.getByLabel("Your phone number").fill("0000 0001");
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByText(/If \+85200000001 is on the list/)).toBeVisible();
  await page.getByLabel("The code").fill("123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(/\/today$/);
});

test("another country code is used when it is picked", async ({ page }) => {
  await asNewVisitor(page);
  await page.goto("/en/login");
  await page.getByLabel("Country or region code").selectOption("GB");
  await page.getByLabel("Your phone number").fill("07700 900123");
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByText(/If \+447700900123 is on the list/)).toBeVisible();
});
