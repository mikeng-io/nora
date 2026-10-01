import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// The three views of one stored document (design/artifact §32): present (one Section per
// slide), print (the document and nothing else), and history (an old revision read-only).
// Against the mock API: Lisbon in December is at revision 3, with one Section and a picture
// after it, so it presents as a cover and one slide. Nothing here restores a revision: the
// mock's state is shared by every spec in the run (the restore round trip is
// tests/web/test_document_history_api.py and dev/mock-history.test.ts).
const LISBON = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01";
const MEIS = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e04";

async function seriousViolations(page: Page) {
  // The entrance fade runs from opacity 0: contrast read mid-fade is the animation's, not
  // the page's, so wait for it to finish first.
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running"));
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  return axe.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => [v.id, v.nodes.map((n) => n.target.join(" "))]);
}

test("Present shows one Section per slide, moves on the keyboard, and Escape leaves", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(`/en/documents/${LISBON}`);
  await page.getByRole("link", { name: "Present" }).click();
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}\\?view=present$`));
  const deck = page.getByTestId("present-view");
  await expect(deck).toBeVisible();
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  // The cover is the Page's title; the Section is not on it.
  await expect(deck.getByRole("heading", { level: 1, name: "Lisbon in December" })).toBeVisible();
  await expect(deck.getByRole("heading", { level: 2, name: "Notes" })).toHaveCount(0);
  expect(await seriousViolations(page)).toEqual([]);

  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await expect(deck.getByRole("heading", { level: 2, name: "Notes" })).toBeVisible();
  await expect(deck.getByRole("heading", { level: 1 })).toBeHidden();
  // The picture after the Section travels with it.
  await expect(deck.getByRole("img", { name: "The hotel door" })).toBeVisible();
  // The last slide stops; it does not wrap to the cover.
  await page.keyboard.press(" ");
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  // Space on a focused control presses it; it does not also move a slide.
  await page.getByRole("button", { name: "Next" }).focus();
  await page.keyboard.press(" ");
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await page.getByRole("button", { name: "Previous" }).focus();
  await page.keyboard.press(" ");
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");

  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}$`));
  await expect(page.getByTestId("present-view")).toHaveCount(0);
  errors.expectNone();
});

test("Present has a visible exit control and works in Chinese too", async ({ page }) => {
  await signIn(page, "Miles", "zh-Hant");
  await page.goto(`/zh-Hant/documents/${LISBON}?view=present`);
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  await page.getByRole("button", { name: "下一頁" }).click();
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await page.getByRole("button", { name: "離開" }).click();
  await expect(page).toHaveURL(new RegExp(`/zh-Hant/documents/${LISBON}$`));
});

test("a phone swipe moves a slide", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "touch is the phone's");
  await signIn(page);
  await page.goto(`/en/documents/${LISBON}?view=present`);
  const deck = page.getByTestId("present-view");
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  const swipe = async (from: number, to: number) =>
    deck.evaluate(
      (el, [a, b]) => {
        const touch = (x: number) => new Touch({ identifier: 1, target: el, clientX: x, clientY: 300 });
        el.dispatchEvent(new TouchEvent("touchstart", { bubbles: true, touches: [touch(a)], changedTouches: [touch(a)] }));
        el.dispatchEvent(new TouchEvent("touchend", { bubbles: true, touches: [], changedTouches: [touch(b)] }));
      },
      [from, to],
    );
  await swipe(300, 100);
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await swipe(100, 300);
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
});

test("printing a document keeps the document and drops the portal around it", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto(`/en/documents/${LISBON}`);
  await expect(page.getByRole("heading", { level: 1, name: "Lisbon in December" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Print / save as PDF" })).toBeVisible();
  await page.emulateMedia({ media: "print" });
  const doc = page.locator("[data-print-document]");
  await expect(doc.getByRole("heading", { level: 1, name: "Lisbon in December" })).toBeVisible();
  await expect(doc.getByRole("img", { name: "The hotel door" })).toBeVisible();
  // Everything that is not the document is gone from the page.
  for (const chrome of [
    page.getByTestId("document-actions"),
    page.getByTestId("document-lifecycle"),
    page.getByTestId("document-meta"),
    page.getByRole("link", { name: "Documents", exact: true }).first(),
    testInfo.project.name === "phone" ? page.locator("header").first() : page.getByRole("navigation", { name: "Main" }),
  ]) {
    await expect(chrome).toBeHidden();
  }
  // Only the document's own subtree draws anything: every visible box in the body is it,
  // inside it, or one of its (flattened) ancestors.
  const strays = await page.evaluate(() => {
    const root = document.querySelector("[data-print-document]");
    return [...document.body.querySelectorAll("*")]
      .filter((el) => {
        if (!root || root.contains(el) || el.contains(root)) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
      })
      .map((el) => el.tagName.toLowerCase() + (el.id ? `#${el.id}` : ""));
  });
  expect(strays).toEqual([]);
  await page.emulateMedia({ media: "screen" });
  await expect(page.getByTestId("document-actions")).toBeVisible();
});

test("history lists every revision, an old one opens read-only with a banner and Restore", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(`/en/documents/${LISBON}`);
  await page.getByRole("link", { name: "History" }).click();
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}/history$`));
  const rows = page.getByTestId("revision-list").getByRole("link");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("Revision 3");
  await expect(rows.nth(0)).toContainText("Current");
  await expect(rows.nth(2)).toContainText("By Nora");
  expect(await seriousViolations(page)).toEqual([]);

  await rows.nth(2).click();
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}/revisions/1$`));
  await expect(page.getByTestId("old-revision-banner")).toContainText(
    "You are viewing revision 1 of 3, not the current one.",
  );
  await expect(page.getByText("Draft 1.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Restore revision 1" })).toBeVisible();
  // Read-only: none of the current document's controls are here.
  await expect(page.getByTestId("document-lifecycle")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Present" })).toHaveCount(0);
  expect(await seriousViolations(page)).toEqual([]);
  // The banner is the screen's; on paper only the revision prints.
  await expect(page.getByTestId("printed-revision")).toBeHidden();
  await page.emulateMedia({ media: "print" });
  await expect(page.getByTestId("old-revision-banner")).toBeHidden();
  await expect(page.getByText("Draft 1.")).toBeVisible();
  // ...but the paper still says it is an old revision, never passing for the current one.
  await expect(page.getByTestId("printed-revision")).toHaveText(
    "Revision 1 of 3: not the current version of this document.",
  );
  errors.expectNone();
});

test("history of a document Miles may not read looks exactly like an unknown one", async ({ page }) => {
  await signIn(page);
  for (const path of [
    `/en/documents/${MEIS}/history`,
    `/en/documents/00000000-0000-4000-8000-000000000000/history`,
    `/en/documents/${MEIS}/revisions/1`,
    `/en/documents/${LISBON}/revisions/9`,
    `/en/documents/${LISBON}/revisions/x`,
  ]) {
    await page.goto(path);
    await expect(page.getByText("Not one of your documents"), path).toBeVisible();
  }
});
