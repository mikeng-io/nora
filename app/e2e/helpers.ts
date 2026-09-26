import { expect, type Page } from "@playwright/test";

/** Sign in through the dev-only picker as someone from dev/identity.dev.toml. */
export async function signIn(page: Page, name = "Miles", locale = "en") {
  await page.goto(`/${locale}/login`);
  await page.getByRole("button", { name, exact: true }).click();
  await page.waitForURL(/\/today$/);
}

/** Collect uncaught errors and console errors for the life of a page. */
export function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    // A WebGL driver note from the headless GPU is not the app's error.
    if (/GL Driver Message|WebGL/.test(text)) return;
    errors.push(`console: ${text}`);
  });
  return {
    expectNone: () => expect(errors, errors.join("\n")).toEqual([]),
  };
}
