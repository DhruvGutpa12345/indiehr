import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it } from "node:test";

import { formatInr, formatInrCompact, groupIndianDigits } from "./money";

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

describe("compact amounts for the Owner's tiles (P1-T11c3)", () => {
  const labels = { lakh: "lakh", crore: "crore" };

  it("shows crores and lakhs with two decimals, words passed in", () => {
    assert.equal(formatInrCompact("12345678.90", labels), "₹1.23 crore");
    assert.equal(formatInrCompact("10000000", labels), "₹1.00 crore");
    assert.equal(formatInrCompact("456000", labels), "₹4.56 lakh");
    assert.equal(formatInrCompact("100000", labels), "₹1.00 lakh");
    assert.equal(
      formatInrCompact("456000", { lakh: "लाख", crore: "करोड़" }),
      "₹4.56 लाख",
    );
    assert.equal(
      formatInrCompact("12345678", { lakh: "लाख", crore: "करोड़" }),
      "₹1.23 करोड़",
    );
  });

  it("rounds half up, and carries a rounded-up lakh into a crore", () => {
    assert.equal(formatInrCompact("112500", labels), "₹1.13 lakh");
    assert.equal(formatInrCompact("1234500", labels), "₹12.35 lakh");
    assert.equal(formatInrCompact("9999999.6", labels), "₹1.00 crore");
    assert.equal(formatInrCompact("12350000", labels), "₹1.24 crore");
  });

  it("below one lakh it is exactly formatInr", () => {
    assert.equal(formatInrCompact("98765", labels), formatInr("98765"));
    assert.equal(formatInrCompact("98765", labels), "₹98,765.00");
    assert.equal(formatInrCompact("99999.999", labels), "₹1,00,000.00");
    assert.equal(formatInrCompact("0", labels), "₹0.00");
  });

  it("keeps the sign in front", () => {
    assert.equal(formatInrCompact("-456000", labels), "-₹4.56 lakh");
    assert.equal(formatInrCompact("-98765", labels), "-₹98,765.00");
  });

  it("never makes up a figure for a missing value", () => {
    assert.equal(formatInrCompact(null, labels), "-");
    assert.equal(formatInrCompact(undefined, labels), "-");
    assert.equal(formatInrCompact("", labels), "-");
    assert.equal(formatInrCompact("abc", labels), "abc");
  });
});
