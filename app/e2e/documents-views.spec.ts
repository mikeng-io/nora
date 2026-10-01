import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn, watchErrors } from "./helpers";

// The three views of one stored document (design/artifact §34): present (one Section per
// slide), print (the document and nothing else), and history (an old revision read-only).
// Against the mock API: Lisbon in December is at revision 3, with one Section and a picture
// after it, so it presents as a cover and one slide. Nothing here restores a revision: the
// mock's state is shared by every spec in the run (the restore round trip is
// tests/web/test_document_history_api.py and dev/mock-history.test.ts).
const LISBON = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01";
const MEIS = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e04";
const ROTA = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e02";
// A link carries the 12-hex handle; the page resolves it to this UUID.
const PACKING = "9e1d2c3b-4a5f-4617-8899-aabbccddeeff";
const PACKING_HANDLE = "9e1d2c3b4a5f";

const MOCK = `http://127.0.0.1:${process.env.MOCK_PORT ?? 9199}`;
// The archived test changes a document's state: the mock is put back after each test.
test.afterEach(async ({ request }) => {
  await request.post(`${MOCK}/__mock/documents/reset`);
});

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
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}\\?view=present`));
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
  // Focus is back on the Present button, and leaving went BACK: one more Back leaves the
  // document, so the deck left no second copy of the page in the history.
  await expect(page.getByTestId("present-button")).toBeFocused();
  await page.goBack();
  await expect(page).not.toHaveURL(new RegExp(`/documents/${LISBON}`));
  errors.expectNone();
});

test("Present is a modal in effect: the page behind is inert and Tab stays in the deck", async ({ page }) => {
  await signIn(page);
  await page.goto(`/en/documents/${LISBON}?view=present`);
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  expect(await page.evaluate(() => Boolean(document.querySelector("main")?.closest("[inert]")))).toBe(true);
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press(i % 3 === 2 ? "Shift+Tab" : "Tab");
    expect(
      await page.evaluate(() => Boolean(document.activeElement?.closest("[data-present-deck]"))),
      `Tab ${i}`,
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("present-view")).toHaveCount(0);
  expect(await page.evaluate(() => Boolean(document.querySelector("main")?.closest("[inert]")))).toBe(false);
});

test("a focused code block keeps its arrows, the slide stays in the URL, and a reload keeps it", async ({ page }) => {
  await signIn(page);
  await page.goto(`/en/documents/${LISBON}?view=present`);
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await expect(page).toHaveURL(/[?&]slide=2(&|$)/);
  // The code block is a scroll region: its arrow keys scroll it, they do not turn slides.
  await page.getByTestId("present-view").getByRole("region", { name: "text code" }).focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Space");
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  // Escape is never the block's.
  await page.reload();
  await expect(page.getByTestId("slide-counter")).toHaveText("2 / 2");
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(new RegExp(`/documents/${LISBON}$`));
});

test("Present opened from a link's handle draws the pictures by the document's own id", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto(`/en/documents/${PACKING_HANDLE}?view=present`);
  await expect(page.getByTestId("slide-counter")).toHaveText("1 / 2");
  await page.keyboard.press("ArrowRight");
  const img = page.getByTestId("present-view").getByRole("img", { name: "The bag" });
  await expect(img).toBeVisible();
  await expect(img).toHaveAttribute("src", `/api/v1/documents/${PACKING}/media/${"b".repeat(64)}`);
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(new RegExp(`/documents/${PACKING_HANDLE}$`));
  errors.expectNone();
});

test("the Print button in the dark theme prints its diagram redrawn light, then puts the theme back", async ({ page }) => {
  await signIn(page);
  await page.goto(`/en/documents/${PACKING}`);
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
  });
  const figure = page.locator("[data-print-document] [data-mermaid-theme]");
  await expect(figure).toHaveAttribute("data-mermaid-theme", "dark");
  // window.print, recorded instead of opening a dialog: what the paper would have shown.
  await page.evaluate(() => {
    const w = window as unknown as { printedWith?: string[] };
    window.print = () => {
      w.printedWith = [
        document.documentElement.dataset.theme ?? "",
        ...[...document.querySelectorAll("[data-mermaid-theme]")].map((e) => e.getAttribute("data-mermaid-theme") ?? ""),
      ];
    };
  });
  await page.getByRole("button", { name: "Print / save as PDF" }).click();
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { printedWith?: string[] }).printedWith ?? null))
    .toEqual(["light", "light"]);
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
});

test("an archived document prints a line saying it is archived", async ({ page }) => {
  await signIn(page);
  await page.goto(`/en/documents/${ROTA}`);
  await page.getByRole("button", { name: "Archive" }).click();
  await expect(page.getByTestId("document-lifecycle").getByRole("button", { name: "Restore" })).toBeVisible();
  try {
    await expect(page.getByTestId("printed-archived")).toBeHidden();
    await page.emulateMedia({ media: "print" });
    await expect(page.getByTestId("printed-archived")).toHaveText(
      "Archived: this document is off the list and Nora won't change it until it is restored.",
    );
    await expect(page.getByTestId("document-lifecycle")).toBeHidden();
  } finally {
    await page.emulateMedia({ media: "screen" });
    await page.getByTestId("document-lifecycle").getByRole("button", { name: "Restore" }).click();
    await expect(page.getByTestId("document-lifecycle").getByRole("button", { name: "Archive" })).toBeVisible();
  }
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
  // ...and inside it, the page's own controls and meta (drawn under the title) are gone:
  // no button prints, and no line but the document's own.
  const printedControls = await page.evaluate(() =>
    [...document.querySelectorAll("[data-print-document] button, [data-print-document] [data-testid]")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      })
      .map((el) => el.getAttribute("data-testid") ?? el.textContent?.trim()),
  );
  expect(printedControls).toEqual([]);
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
