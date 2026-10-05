import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import zh from "../../messages/zh-Hant.json";
import { DEFAULT_REGION, DIAL_REGIONS, flag, fullNumber, safeRegion } from "./dial-codes";

describe("dial codes", () => {
  it("defaults to Hong Kong, first in the list", () => {
    expect(DEFAULT_REGION).toBe("HK");
    expect(DIAL_REGIONS[0]).toEqual({ region: "HK", dial: "852" });
  });

  it("joins a local number to the chosen country code", () => {
    expect(fullNumber("HK", "9123 4567")).toBe("+85291234567");
    expect(fullNumber("MO", "6612-3456")).toBe("+85366123456");
  });

  it("keeps a Hong Kong number's digits whole: it has no trunk zero", () => {
    expect(fullNumber("HK", "0000 0001")).toBe("+85200000001");
  });

  it("drops a trunk zero where the country has one", () => {
    expect(fullNumber("GB", "07700 900123")).toBe("+447700900123");
    expect(fullNumber("AU", "(04) 1234 5678")).toBe("+61412345678");
  });

  it("keeps a number typed with its own + whatever region is chosen", () => {
    expect(fullNumber("HK", "+44 7700 900123")).toBe("+44 7700 900123");
  });

  it("drops the (0) written after a country code", () => {
    expect(fullNumber("HK", "+44 (0)7700 900123")).toBe("+44 7700 900123");
  });

  it("reads an international prefix as a whole number", () => {
    expect(fullNumber("HK", "00852 9123 4567")).toBe("+85291234567");
    expect(fullNumber("US", "011 852 9123 4567")).toBe("+85291234567");
  });

  it("does not add a second 1 to a North American number typed with it", () => {
    expect(fullNumber("US", "1 415 555 0100")).toBe("+14155550100");
    expect(fullNumber("US", "(415) 555-0100")).toBe("+14155550100");
  });

  it("passes a Signal ACI through untouched", () => {
    const aci = "0b1d0f6e-1c2d-4e3f-8a9b-0c1d2e3f4a5b";
    expect(fullNumber("HK", aci)).toBe(aci);
  });

  it("does not trust a region the list does not hold", () => {
    expect(safeRegion("ZZ").region).toBe("HK");
    expect(fullNumber("ZZ", "91234567")).toBe("+85291234567");
  });

  it("draws a region's flag", () => {
    expect(flag("HK")).toBe("🇭🇰");
  });

  it("names every region in both languages", () => {
    for (const { region } of DIAL_REGIONS) {
      expect(en.login.regions).toHaveProperty(region);
      expect(zh.login.regions).toHaveProperty(region);
    }
  });
});
