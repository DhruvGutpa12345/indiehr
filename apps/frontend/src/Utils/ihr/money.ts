// IndieHR P1-T7b: money shown the Indian way (₹1,00,000.00).
//
// Pure helpers with no app imports, so they run under `node --test` in CI.
// The shared money component (components/ui/monetary-display.tsx) already
// formats with "en-IN"; these helpers cover the few upstream places that
// built the text by hand (plain "100000.00").
import Decimal from "decimal.js";

export const INR_SYMBOL = "₹";

/**
 * Group the digits of a plain decimal string the Indian way:
 * "1234567.50" -> "12,34,567.50", "-2500.5" -> "-2,500.5".
 * The string is not rounded or re-parsed, so no paise are lost.
 */
export function groupIndianDigits(plain: string): string {
  const negative = plain.startsWith("-");
  const unsigned = negative || plain.startsWith("+") ? plain.slice(1) : plain;
  const [whole, fraction] = unsigned.split(".");
  let grouped = whole;
  if (whole.length > 3) {
    const lastThree = whole.slice(-3);
    const rest = whole.slice(0, -3);
    grouped = `${rest.replace(/\B(?=(\d{2})+$)/g, ",")},${lastThree}`;
  }
  const sign = negative ? "-" : "";
  const decimals = fraction === undefined ? "" : `.${fraction}`;
  return `${sign}${grouped}${decimals}`;
}

/**
 * Format an amount as rupees with Indian grouping and a fixed number of
 * decimals: 100000 -> "₹1,00,000.00", -2500.5 -> "-₹2,500.50".
 * Rounds with decimal.js (the app sets ROUND_HALF_UP). Empty values give "-";
 * a value that is not a number is shown unchanged.
 */
export function formatInr(
  value: string | number | Decimal | null | undefined,
  symbol: string = INR_SYMBOL,
  precision = 2,
): string {
  if (value === null || value === undefined || value === "") return "-";
  let fixed: string;
  try {
    fixed = new Decimal(value).toFixed(precision);
  } catch {
    return String(value);
  }
  let negative = fixed.startsWith("-");
  const digits = negative ? fixed.slice(1) : fixed;
  if (/^[0.]+$/.test(digits)) negative = false; // no "-₹0.00"
  return `${negative ? "-" : ""}${symbol}${groupIndianDigits(digits)}`;
}

/** The words of the compact form, passed in (our strings: ihr_amount_lakh, ihr_amount_crore). */
export interface CompactLabels {
  lakh: string;
  crore: string;
}

const LAKH = new Decimal(100000);
const CRORE = new Decimal(10000000);

/**
 * (P1-T11c3, design-c.md §7) An amount for a tile: at least one crore "₹1.23 crore", at least
 * one lakh "₹4.56 lakh", else exactly formatInr. Two decimals, half-up (decimal.js, as
 * formatInr). The sign comes first ("-₹4.56 lakh"). Empty or unreadable values are formatInr's
 * ("-", or the text unchanged), never a made-up zero. The exact figure is formatInr's: callers
 * show it as the tile's title and in the "Exact figures" table.
 */
export function formatInrCompact(
  value: string | number | Decimal | null | undefined,
  labels: CompactLabels,
  symbol: string = INR_SYMBOL,
): string {
  if (value === null || value === undefined || value === "") return "-";
  let amount: Decimal;
  try {
    amount = new Decimal(value);
  } catch {
    return String(value);
  }
  if (!amount.isFinite()) return String(value);
  const size = amount.abs();
  let divisor: Decimal | null = null;
  let word = "";
  if (size.gte(CRORE)) {
    divisor = CRORE;
    word = labels.crore;
  } else if (size.gte(LAKH)) {
    divisor = LAKH;
    word = labels.lakh;
  }
  if (divisor === null) return formatInr(amount, symbol);
  let scaled = amount.div(divisor).toDecimalPlaces(2, Decimal.ROUND_HALF_UP);
  // 99,99,999.6 rounds up to "100.00 lakh": show it as 1.00 crore instead
  if (divisor === LAKH && scaled.abs().gte(100)) {
    divisor = CRORE;
    word = labels.crore;
    scaled = amount.div(divisor).toDecimalPlaces(2, Decimal.ROUND_HALF_UP);
  }
  const negative = scaled.isNegative() && !scaled.isZero();
  return `${negative ? "-" : ""}${symbol}${scaled.abs().toFixed(2)} ${word}`;
}
