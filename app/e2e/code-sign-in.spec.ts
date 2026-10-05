import { expect, test } from "@playwright/test";
import { watchErrors } from "./helpers";

// Sign-in with a one-time code (DEC-287), against the mock: the identifier is
// a dev person_id and the code is always 123456 (dev/mock-nora.mjs).

test("a code on WhatsApp signs in as the same person Discord would", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/en/login");
  await page.getByText("WhatsApp", { exact: true }).click();
  await page.getByLabel("Your phone number").fill("miles");
  await page.getByRole("button", { name: "Send me a code" }).click();

  await page.getByLabel("The code").fill("654321");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("That code didn't work", { exact: false })).toBeVisible();

  await page.getByLabel("The code").fill("123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(/\/today$/);
  await expect(page.getByRole("heading", { name: /Miles/ })).toBeVisible();
  await expect(page.getByText("Signed in with a code").first()).toBeVisible();
  errors.expectNone();
});

test("a number nobody knows is answered the same way, and no code works", async ({ page }) => {
  await page.goto("/en/login");
  await page.getByLabel("Your phone number").fill("+85200000000");
  await page.getByRole("button", { name: "Send me a code" }).click();
  await expect(page.getByText(/If \+85200000000 is on the list/)).toBeVisible();
  await page.getByLabel("The code").fill("123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText("That code didn't work", { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});
