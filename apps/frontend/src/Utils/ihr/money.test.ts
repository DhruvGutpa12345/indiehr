import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import { formatInr, groupIndianDigits } from "./money";

// CI runs this file from apps/frontend (see .github/workflows/apps.yml).
const source = (path: string) =>
  readFileSync(join(process.cwd(), "src", path), "utf-8");

describe("Indian money format (P1-T7b)", () => {
  it("groups digits in lakhs and crores", () => {
    assert.equal(groupIndianDigits("0"), "0");
    assert.equal(groupIndianDigits("999"), "999");
    assert.equal(groupIndianDigits("1000"), "1,000");
    assert.equal(groupIndianDigits("100000.00"), "1,00,000.00");
    assert.equal(groupIndianDigits("12345678.50"), "1,23,45,678.50");
    assert.equal(groupIndianDigits("-2500.5"), "-2,500.5");
  });

  it("formats rupees with two decimals", () => {
    assert.equal(formatInr(100000), "₹1,00,000.00");
    assert.equal(formatInr("12345678.5"), "₹1,23,45,678.50");
    assert.equal(formatInr("100000.000000"), "₹1,00,000.00");
    assert.equal(formatInr("1.995"), "₹2.00");
    assert.equal(formatInr("0.004"), "₹0.00");
    assert.equal(formatInr(-2500.5), "-₹2,500.50");
    assert.equal(formatInr("-0.001"), "₹0.00");
  });

  it("handles a custom symbol, precision and empty values", () => {
    assert.equal(formatInr("100000", "Rs. "), "Rs. 1,00,000.00");
    assert.equal(formatInr("1234.5678", "₹", 3), "₹1,234.568");
    assert.equal(formatInr(null), "-");
    assert.equal(formatInr(undefined), "-");
    assert.equal(formatInr(""), "-");
    assert.equal(formatInr("abc"), "abc");
  });

  it("matches the en-IN format of the shared money component", () => {
    const intl = new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
    });
    assert.equal(intl.format(100000), "₹1,00,000.00");
    assert.equal(intl.format(12345678.5), "₹1,23,45,678.50");
    for (const n of [0, 5, 1000, 100000, 12345678.5, -2500.5, 987654321.25]) {
      assert.equal(formatInr(n), intl.format(n));
    }
  });

  // Guards against an upstream sync quietly undoing the Indian format.
  it("upstream money component still uses en-IN and INR", () => {
    const text = source("components/ui/monetary-display.tsx");
    assert.match(text, /CURRENCY_CODE = "INR"/);
    assert.match(text, /numberFormatter = new Intl\.NumberFormat\("en-IN"/);
    assert.match(
      text,
      /numberFormatterWithoutCurrency = new Intl\.NumberFormat\("en-IN"/,
    );
  });

  it("formatComponentValue uses the grouped formatter", () => {
    const text = source("types/base/monetaryComponent/monetaryComponent.ts");
    assert.match(
      text,
      /: formatInr\(value, currencySymbol, ACCOUNTING_PRECISION\);/,
    );
    assert.doesNotMatch(text, /`\$\{currencySymbol\}\$\{round\(value\)\}`/);
  });
});
