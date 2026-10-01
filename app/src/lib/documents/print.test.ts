import { describe, expect, it } from "vitest";
import { markLinksForPrint, PRINT_HREF_ATTR, printHref, printInLight } from "./print";

describe("printInLight", () => {
  it("in the dark theme, switches to light, waits for every diagram to redraw light, prints, then puts it back", async () => {
    const html = { dataset: { theme: "dark" } as DOMStringMap };
    let themes = ["dark", "pending"];
    const seen: Array<{ theme?: string; themes: string[] }> = [];
    let ticks = 0;
    await printInLight({
      html,
      drawnThemes: () => themes,
      print: () => seen.push({ theme: html.dataset.theme, themes: [...themes] }),
      wait: async () => {
        ticks += 1;
        if (ticks === 2) themes = ["light", "light"];
      },
    });
    expect(seen).toEqual([{ theme: "light", themes: ["light", "light"] }]);
    expect(html.dataset.theme).toBe("dark");
  });

  it("in the light theme prints at once and touches nothing", async () => {
    const html = { dataset: {} as DOMStringMap };
    let printed = 0;
    await printInLight({ html, drawnThemes: () => ["dark"], print: () => (printed += 1), wait: async () => {
      throw new Error("must not wait");
    } });
    expect(printed).toBe(1);
    expect(html.dataset.theme).toBeUndefined();
  });

  it("prints anyway after the timeout, and restores the theme even when printing throws", async () => {
    const html = { dataset: { theme: "dark" } as DOMStringMap };
    await expect(
      printInLight({ html, drawnThemes: () => ["dark"], print: () => { throw new Error("blocked"); }, wait: async () => {}, timeoutMs: 200 }),
    ).rejects.toThrow("blocked");
    expect(html.dataset.theme).toBe("dark");
  });
});

describe("printHref", () => {
  it("prints the address after a link whose text does not already say it", () => {
    expect(printHref("https://example.org/trip", "the itinerary")).toBe("https://example.org/trip");
    expect(printHref("mailto:hello@example.org", "write to us")).toBe("hello@example.org");
  });

  it("does not print an address the link text already is", () => {
    for (const text of ["https://example.org/trip", "example.org/trip", "www.example.org/trip/", " EXAMPLE.org/trip "]) {
      expect(printHref("https://www.example.org/trip", text), text).toBeNull();
    }
    expect(printHref("mailto:hello@example.org", "hello@example.org")).toBeNull();
  });

  it("never prints an address a reader cannot go to", () => {
    for (const href of [null, undefined, "", "#section", "/documents/x", "javascript:alert(1)", "tel:123"]) {
      expect(printHref(href, "x"), String(href)).toBeNull();
    }
  });
});

describe("markLinksForPrint", () => {
  // A stand-in for the DOM with the three calls the function makes; the suite runs in node.
  function link(href: string, text: string) {
    const attrs = new Map<string, string>([["href", href], [PRINT_HREF_ATTR, "stale"]]);
    return {
      textContent: text,
      getAttribute: (k: string) => attrs.get(k) ?? null,
      setAttribute: (k: string, v: string) => void attrs.set(k, v),
      removeAttribute: (k: string) => void attrs.delete(k),
      attrs,
    };
  }

  it("marks the links that need their address and clears the rest", () => {
    const a = link("https://example.org/a", "a guide");
    const b = link("https://example.org/b", "example.org/b");
    const root = { querySelectorAll: () => [a, b] } as unknown as ParentNode;
    markLinksForPrint(root);
    expect(a.attrs.get(PRINT_HREF_ATTR)).toBe("https://example.org/a");
    expect(b.attrs.has(PRINT_HREF_ATTR)).toBe(false);
  });
});
