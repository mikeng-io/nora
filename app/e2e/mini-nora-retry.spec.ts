import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";

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
  const renderedPose = async () => {
    const canvas = page.locator("canvas");
    await canvas.scrollIntoViewIfNeeded();
    const box = (await canvas.boundingBox())!;
    // Exclude the parent panel's rounded-edge antialiasing from pose comparison.
    const pixels = await page.screenshot({ clip: { x: box.x + 4, y: box.y + 4, width: box.width - 8, height: box.height - 8 } });
    return createHash("sha256").update(pixels).digest("hex");
  };
  const standing = await renderedPose();
  await page.getByRole("combobox", { name: "Motion", exact: true }).selectOption("Idle");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Motion", exact: true }).selectOption("Stand");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeDisabled();
  await page.getByRole("combobox", { name: "Motion", exact: true }).selectOption("Run");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect.poll(renderedPose).not.toBe(standing);
  await page.getByRole("combobox", { name: "Motion", exact: true }).selectOption("Stand");
  await expect.poll(renderedPose).toBe(standing);
});
