import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDecimal,
  formatDuration,
  formatInteger,
  formatMoney,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatR,
  humanise,
  pnlClass,
  signOf,
} from "./format";

/**
 * These tests exist because this module is the frontend's whole defence against the float bug.
 * Every case below is one that `Number(value).toFixed(n)` would get wrong, or one where "no
 * value" must not be rendered as zero.
 */
describe("formatDecimal", () => {
  it("groups thousands without going through Number", () => {
    expect(formatDecimal("1234567.89")).toBe("1,234,567.89");
    expect(formatDecimal("999.5", { places: 0 })).toBe("1,000");
  });

  it("keeps precision a double would lose", () => {
    // Number("9007199254740993") === 9007199254740992 — beyond Number.MAX_SAFE_INTEGER.
    expect(formatDecimal("9007199254740993", { places: 0 })).toBe("9,007,199,254,740,993");
    // Number(1.005).toFixed(2) === "1.00" because 1.005 is stored as 1.00499999999999989.
    expect(formatDecimal("1.005")).toBe("1.01");
    expect(formatDecimal("2.675")).toBe("2.68");
  });

  it("carries through the integer part when the fraction overflows", () => {
    expect(formatDecimal("9.999")).toBe("10.00");
    expect(formatDecimal("-9.999")).toBe("-10.00");
    expect(formatDecimal("99999.995")).toBe("100,000.00");
  });

  it("pads a short fraction rather than truncating the value", () => {
    expect(formatDecimal("5")).toBe("5.00");
    expect(formatDecimal("5.1")).toBe("5.10");
  });

  it("renders an em dash for an undefined value, never zero", () => {
    expect(formatDecimal(null)).toBe("—");
    expect(formatDecimal(undefined)).toBe("—");
    expect(formatDecimal("")).toBe("—");
    expect(formatDecimal("not a number")).toBe("—");
    expect(formatDecimal(null, { fallback: "n/a" })).toBe("n/a");
  });

  it("distinguishes a real zero from a missing value", () => {
    expect(formatDecimal("0")).toBe("0.00");
  });

  it("does not render a signed or negative zero", () => {
    expect(formatDecimal("-0.001")).toBe("0.00");
    expect(formatDecimal("0", { signed: true })).toBe("0.00");
  });

  it("prefixes a plus only on positive values when asked", () => {
    expect(formatDecimal("12.5", { signed: true })).toBe("+12.50");
    expect(formatDecimal("-12.5", { signed: true })).toBe("-12.50");
  });
});

describe("formatMoney", () => {
  it("places the symbol inside the sign", () => {
    expect(formatMoney("1234.5", "USD")).toBe("$1,234.50");
    expect(formatMoney("-1234.5", "USD")).toBe("-$1,234.50");
    expect(formatMoney("1234.5", "USD", { signed: true })).toBe("+$1,234.50");
  });

  it("uses each currency's minor units", () => {
    expect(formatMoney("1234", "JPY")).toBe("¥1,234");
    expect(formatMoney("1234.5", "EUR")).toBe("€1,234.50");
  });

  it("falls back to the code for a currency with no symbol", () => {
    expect(formatMoney("10", "SEK")).toBe("SEK 10.00");
  });

  it("passes an undefined amount straight through", () => {
    expect(formatMoney(null, "USD")).toBe("—");
  });
});

describe("unit suffixes", () => {
  it("formats percentages and R multiples", () => {
    expect(formatPercent("62.5")).toBe("62.50%");
    expect(formatR("1.5")).toBe("+1.50R");
    expect(formatR("-1")).toBe("-1.00R");
    expect(formatPercent(null)).toBe("—");
    expect(formatR(null)).toBe("—");
  });

  it("trims trailing zeros from a quantity but keeps real precision", () => {
    expect(formatQuantity("100.00")).toBe("100");
    expect(formatQuantity("0.5")).toBe("0.5");
    expect(formatQuantity("0.00012345")).toBe("0.00012345");
    expect(formatQuantity(null)).toBe("—");
  });

  it("caps a price's fractional tail without touching its leading digits", () => {
    // A derived average fill price can carry a long tail. Showing it raw is what this exists to
    // prevent; the value itself is unchanged server-side.
    // ...589764 rounds up at the sixth decimal, and the result keeps its place count.
    expect(formatPrice("50953.860589764")).toBe("50,953.860590");
    expect(formatPrice("45.4757707937")).toBe("45.475771");
    expect(formatPrice("189.5")).toBe("189.50");
    expect(formatPrice("189")).toBe("189.00");
    expect(formatPrice("0.00012345")).toBe("0.000123");
    expect(formatPrice(null)).toBe("—");
  });

  it("formats integers", () => {
    expect(formatInteger(1234567)).toBe("1,234,567");
    expect(formatInteger(0)).toBe("0");
    expect(formatInteger(null)).toBe("—");
  });
});

describe("signOf", () => {
  it("reads the sign textually", () => {
    expect(signOf("0.01")).toBe(1);
    expect(signOf("-0.01")).toBe(-1);
    expect(signOf("0")).toBe(0);
    expect(signOf("0.000")).toBe(0);
    expect(signOf("-0.000")).toBe(0);
  });

  it("treats a missing or unparseable value as neutral, not as a loss", () => {
    expect(signOf(null)).toBe(0);
    expect(signOf(undefined)).toBe(0);
    expect(signOf("pending")).toBe(0);
  });

  it("drives the P&L colour", () => {
    expect(pnlClass("5")).toBe("text-profit");
    expect(pnlClass("-5")).toBe("text-loss");
    expect(pnlClass("0")).toBe("text-muted");
    expect(pnlClass(null)).toBe("text-muted");
  });
});

describe("formatDuration", () => {
  it("scales the unit to the magnitude", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(600)).toBe("10m");
    expect(formatDuration(3600)).toBe("1h");
    expect(formatDuration(8100)).toBe("2h 15m");
    expect(formatDuration(86400)).toBe("1d");
    expect(formatDuration(100800)).toBe("1d 4h");
  });

  it("distinguishes an unknown hold time from an instant one", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(0)).toBe("0s");
  });

  it("refuses to present a negative hold as a measurement", () => {
    expect(formatDuration(-2559312)).toBe("—");
    expect(formatDuration(-1)).toBe("—");
  });
});

describe("misc", () => {
  it("formats byte sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("humanises enum values", () => {
    expect(humanise("stop_limit")).toBe("Stop limit");
    expect(humanise(null)).toBe("—");
    expect(humanise(null, "None")).toBe("None");
  });
});
