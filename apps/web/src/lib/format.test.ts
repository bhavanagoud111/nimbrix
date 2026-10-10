import { describe, expect, it } from "vitest";
import { compactMoney, money, pct, ratio, shortDate } from "./format";

describe("format", () => {
  it("formats decimal strings without changing values", () => {
    expect(money("280.320000")).toBe("$280.32");
    expect(money("-5.00")).toBe("-$5.00");
    expect(money(null)).toBe("—");
    expect(money("0.0416", "USD", 4)).toBe("$0.0416");
    expect(money("99.99", "EUR")).toBe("€99.99");
  });
  it("formats percentages and ratios", () => {
    expect(pct("160.00")).toBe("+160.0%");
    expect(pct("-3.5")).toBe("-3.5%");
    expect(pct(null)).toBe("—");
    expect(ratio("0.9000")).toBe("90.0%");
  });
  it("uses UTC dates", () => {
    expect(shortDate("2026-09-30")).toBe("Sep 30");
    expect(compactMoney(123456)).toBe("$123.5K");
  });
});
