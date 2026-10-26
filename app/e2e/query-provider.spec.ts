import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test("a sign-out broadcast ends a mounted tab's session before redirecting", async ({ page }) => {
  await signIn(page);
  await page.evaluate(async () => {
    // A second channel models another tab announcing forgetDevice before
    // that tab's server action has cleared their shared session cookie.
    const channel = new BroadcastChannel("nora:device-sign-out");
    const session = await fetch("/api/auth/session").then((r) => r.json());
    channel.postMessage({ type: "signed-out", sessionId: session.user.sessionId });
    channel.close();
  });
  await expect(page).toHaveURL(/\/en\/login/);
  await page.reload();
  await expect(page).toHaveURL(/\/en\/login/);
  await expect(page.getByRole("button", { name: "Miles", exact: true })).toBeVisible();
});

test("a delayed receiver request cannot clear a newer sign-in", async ({ page, context }) => {
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const sender = await context.newPage();
  await sender.goto("/en/today");
  await expect(sender.getByRole("heading", { level: 1 })).toBeVisible();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let captured!: () => void;
  const held = new Promise<void>((resolve) => { captured = resolve; });
  await page.route("**/*", async (route) => {
    if (route.request().method() === "POST" && route.request().headers()["next-action"]) {
      captured();
      await gate;
    }
    await route.continue();
  });
  try {
    await sender.getByRole("button", { name: /Account and settings/ }).click();
    await sender.getByRole("menuitem", { name: "Sign out", exact: true }).click();
    await held;
    await expect(sender).toHaveURL(/\/en\/login/);
    // The same person's second sign-in must also survive the old generation.
    await sender.getByRole("button", { name: "Miles", exact: true }).click();
    await expect(sender).toHaveURL(/\/en\/today/);
    const response = page.waitForResponse((r) =>
      r.request().method() === "POST" && !!r.request().headers()["next-action"]);
    release();
    await response;
    await sender.reload();
    await expect(sender).toHaveURL(/\/en\/today/);
  } finally {
    release();
    await sender.close();
  }
});
