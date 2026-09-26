import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./helpers";

// What the device keeps belongs to whoever is signed in, and to nobody
// after: a shared phone never hands one person's reads to the next.

async function storedKeys(page: Page): Promise<string[]> {
  return page.evaluate(
    () =>
      new Promise<string[]>((resolve) => {
        const open = indexedDB.open("keyval-store");
        open.onsuccess = () => {
          const db = open.result;
          if (!db.objectStoreNames.contains("keyval")) return resolve([]);
          const req = db.transaction("keyval").objectStore("keyval").getAllKeys();
          req.onsuccess = () => resolve(req.result.map(String));
          req.onerror = () => resolve([]);
        };
        open.onerror = () => resolve([]);
      }),
  );
}

test("signing out forgets the device's stored reads, and nothing writes them back", async ({ page, isMobile }) => {
  await signIn(page);
  await page.goto("/en/people/miles?tab=memory");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Miles");
  await expect.poll(() => storedKeys(page), { timeout: 10_000 }).toContain("nora-cache:miles");

  if (isMobile) {
    await page.getByRole("button", { name: "Your account" }).click();
    await page.getByRole("button", { name: "Sign out" }).click();
  } else {
    await page.getByRole("button", { name: /Account and settings/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
  }
  await page.waitForURL(/\/login/);
  // Past the persister's 2s throttle: a late write would have landed by now.
  await page.waitForTimeout(3000);
  expect(await storedKeys(page)).toEqual([]);
});

test("the login page forgets whatever an earlier session left", async ({ page }) => {
  await signIn(page, "Ken");
  await page.goto("/en/today");
  await expect.poll(() => storedKeys(page), { timeout: 10_000 }).toContain("nora-cache:ken");
  // The session ends without a sign-out (expiry, a cleared cookie).
  await page.context().clearCookies();
  await page.goto("/en/login");
  await expect.poll(() => storedKeys(page), { timeout: 5_000 }).toEqual([]);
});
