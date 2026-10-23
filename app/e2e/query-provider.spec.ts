import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("a sign-out broadcast ends a mounted tab's session before redirecting", async ({ page }) => {
  await signIn(page);
  await page.evaluate(() => {
    // A second channel models another tab announcing forgetDevice before
    // that tab's server action has cleared their shared session cookie.
    const channel = new BroadcastChannel("nora:device-sign-out");
    channel.postMessage("signed-out");
    channel.close();
  });
  await expect(page).toHaveURL(/\/en\/login/);
  await page.reload();
  await expect(page).toHaveURL(/\/en\/login/);
  await expect(page.getByRole("button", { name: "Miles", exact: true })).toBeVisible();
});
