import { test, expect } from "@playwright/test";

test("retries a failed model load and a lost WebGL context", async ({ page }) => {
  await page.goto("/en/login");
  await page.getByRole("button", { name: "Miles", exact: true }).click();
  await page.waitForURL(/\/today$/);
  await page.goto("/en/nora/showcase");
  const model = "**/models/mini-nora/character.glb";
  await page.route(model, route => route.abort(), { times: 1 });
  await page.getByRole("button", { name: "Explore in 3D", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("3D is unavailable");
  await expect(page.getByRole("button", { name: "Try 3D again", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Try 3D again", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Drag to orbit", { timeout: 30_000 });
  await page.getByRole("combobox", { name: "Expression", exact: true }).selectOption("Puzzled");
  await page.getByRole("slider", { name: "Head turn", exact: true }).press("End");
  await page.evaluate(() => {
    const canvas = document.querySelector("canvas")!;
    const gl = canvas.getContext("webgl2")!;
    const extension = gl.getExtension("WEBGL_lose_context");
    if (!extension) throw new Error("WEBGL_lose_context unavailable");
    extension.loseContext();
  });
  await expect(page.getByRole("status")).toContainText("3D is unavailable");
  await page.getByRole("button", { name: "Try 3D again", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Drag to orbit", { timeout: 30_000 });
  await expect(page.locator("canvas")).toHaveCount(1);
  await expect(page.getByRole("combobox", { name: "Expression", exact: true })).toHaveValue("Neutral");
  await expect(page.getByRole("slider", { name: "Head turn", exact: true })).toHaveValue("0");
  await page.getByRole("combobox", { name: "Motion", exact: true }).selectOption("Run");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeEnabled();
});
