import { describe, expect, it } from "vitest";
import { firstGrapheme, telHref } from "./contact-phone";

describe("telHref", () => {
  it("keeps digits and a leading plus", () => {
    expect(telHref("+852 9123-4567")).toBe("tel:+85291234567");
    expect(telHref("9123 4567")).toBe("tel:91234567");
  });
  it("sanitises whatever the schema should have refused", () => {
    expect(telHref("+852 (9123) 4567;ext=1")).toBe("tel:+852912345671");
    expect(telHref("javascript:alert(12345)")).toBe("tel:12345");
    expect(telHref("12+3456")).toBe("tel:123456");
  });
  it("is null under 5 digits", () => {
    expect(telHref("")).toBeNull();
    expect(telHref("+12 34")).toBeNull();
    expect(telHref("call me")).toBeNull();
    expect(telHref("12345")).toBe("tel:12345");
  });
});

describe("firstGrapheme", () => {
  it("takes one CJK character, one whole emoji, one Latin letter upper-cased", () => {
    expect(firstGrapheme("陳大文")).toBe("陳");
    expect(firstGrapheme("  ken")).toBe("K");
    expect(firstGrapheme("👨‍👩‍👧 Family")).toBe("👨‍👩‍👧");
    expect(firstGrapheme("")).toBe("?");
  });
});
