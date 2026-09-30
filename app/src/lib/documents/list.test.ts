import { describe, expect, it } from "vitest";
import { isRevised, pageCount, platformLabel } from "./list";

describe("documents list helpers", () => {
  it("names a platform the way a person would, and never drops an unknown one", () => {
    expect(platformLabel("signal")).toBe("Signal");
    expect(platformLabel("WhatsApp")).toBe("WhatsApp");
    expect(platformLabel("matrix")).toBe("Matrix");
    expect(platformLabel("")).toBe("");
  });

  it("counts pages, with an empty list as page one and nonsense as page one", () => {
    expect(pageCount(0, 20)).toBe(1);
    expect(pageCount(20, 20)).toBe(1);
    expect(pageCount(21, 20)).toBe(2);
    expect(pageCount(5, 0)).toBe(1);
    expect(pageCount(Number.NaN, 20)).toBe(1);
  });

  it("calls only a later revision revised", () => {
    expect(isRevised(1)).toBe(false);
    expect(isRevised(2)).toBe(true);
  });
});
