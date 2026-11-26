import { describe, it, expect } from "vitest";
import { COUNTRIES, COUNTRY_OPTIONS, DEFAULT_DIAL_CODE, joinPhone, splitPhone } from "./phone";

describe("country list", () => {
  it("defaults to India and includes Ireland", () => {
    expect(DEFAULT_DIAL_CODE).toBe("+91");
    expect(COUNTRY_OPTIONS[0]).toMatchObject({ iso: "IN", code: "+91" });
    expect(COUNTRIES).toContainEqual({ name: "Ireland", iso: "IE", code: "+353" });
  });

  it("has unique ISO codes", () => {
    const isos = COUNTRIES.map((c) => c.iso);
    expect(new Set(isos).size).toBe(isos.length);
  });
});

describe("joinPhone", () => {
  it.each([
    ["+91", "98765 43210", "+919876543210"],
    ["+91", "098765-43210", "+919876543210"],
    ["+91", "+91 98765 43210", "+919876543210"],
    ["+91", "919876543210", "+919876543210"],
    ["+353", "087 123 4567", "+353871234567"],
    ["+353", "87 123 4567", "+353871234567"],
    ["+353", "353 87 123 4567", "+353871234567"],
    ["+353", "00353871234567", "+353871234567"],
    ["+91", "+353 87 123 4567", "+353871234567"], // full number typed: its own country wins
  ])("%s + %s -> %s", (code, typed, expected) => {
    expect(joinPhone(code, typed)).toBe(expected);
  });

  it("returns empty for empty input", () => {
    expect(joinPhone("+353", "")).toBe("");
    expect(joinPhone("+353", "   ")).toBe("");
    expect(joinPhone("+353", "0")).toBe("");
  });
});

describe("splitPhone", () => {
  it("splits stored E.164 numbers by country", () => {
    expect(splitPhone("+919876543210")).toEqual({ code: "+91", national: "9876543210" });
    expect(splitPhone("+353871234567")).toEqual({ code: "+353", national: "871234567" });
    expect(splitPhone("+91 98765 43210")).toEqual({ code: "+91", national: "9876543210" });
  });

  it("keeps legacy values without + as they are", () => {
    expect(splitPhone("9876543210")).toEqual({ code: "+91", national: "9876543210" });
    expect(splitPhone("")).toEqual({ code: "+91", national: "" });
    expect(splitPhone(null)).toEqual({ code: "+91", national: "" });
  });

  it("round-trips through joinPhone", () => {
    for (const stored of ["+919876543210", "+353871234567", "+447911123456"]) {
      const { code, national } = splitPhone(stored);
      expect(joinPhone(code, national)).toBe(stored);
    }
  });
});
