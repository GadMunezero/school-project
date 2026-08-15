/**
 * Display formatting for values the API sends as decimal **strings**.
 *
 * The rule this module exists to enforce: the frontend never does arithmetic on money. The
 * backend is authoritative for every P&L, R multiple and percentage; this file only decides how
 * to *show* what it sent.
 *
 * Formatting therefore operates on the decimal string directly — grouping digits and trimming
 * the fraction textually — rather than routing through `Number`. A JS number is an IEEE double
 * and cannot represent `0.1` exactly, so parsing "1234567.89" to format it would introduce error
 * into a figure the backend computed precisely.
 *
 * `toChartNumber` is the one deliberate exception: charting libraries take numbers, and a pixel
 * position does not need sub-cent precision. It is named so that its use is visible in review.
 */

/** A decimal value as the API sends it. `null` means *undefined*, not zero. */
export type DecimalString = string | null | undefined;

const NUMERIC = /^-?\d+(\.\d+)?$/;

interface Parsed {
  negative: boolean;
  integer: string;
  fraction: string;
}

function parse(value: string): Parsed | null {
  const trimmed = value.trim();
  if (!NUMERIC.test(trimmed)) return null;
  const negative = trimmed.startsWith("-");
  const unsigned = negative ? trimmed.slice(1) : trimmed;
  const [integer = "0", fraction = ""] = unsigned.split(".");
  return { negative, integer, fraction };
}

/** Round a decimal string to `places` using half-up on the string itself. */
function roundFraction(parsed: Parsed, places: number): Parsed {
  const { integer, fraction } = parsed;
  if (fraction.length <= places) {
    return { ...parsed, fraction: fraction.padEnd(places, "0") };
  }

  const keep = fraction.slice(0, places);
  const nextDigit = Number(fraction[places] ?? "0");
  if (nextDigit < 5) return { ...parsed, fraction: keep };

  // Propagate the carry through the fraction and, if it overflows, into the integer part.
  const digits = (integer + keep).split("");
  let index = digits.length - 1;
  let carry = 1;
  while (index >= 0 && carry) {
    const sum = Number(digits[index]) + carry;
    digits[index] = String(sum % 10);
    carry = sum >= 10 ? 1 : 0;
    index -= 1;
  }
  const carried = (carry ? "1" : "") + digits.join("");
  const splitAt = carried.length - places;
  return {
    negative: parsed.negative,
    integer: carried.slice(0, splitAt) || "0",
    fraction: places > 0 ? carried.slice(splitAt) : "",
  };
}

function group(integer: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export interface NumberFormatOptions {
  /** Decimal places. Money defaults to 2, ratios to 2, percentages to 2. */
  places?: number;
  /** Prefix a `+` on positive values (useful for P&L deltas). */
  signed?: boolean;
  /** Rendered when the value is null/undefined/unparseable. */
  fallback?: string;
}

/**
 * Format a decimal string with thousands separators and a fixed fraction.
 * Returns `fallback` (default "—") for null — an undefined metric must not render as "0.00".
 */
export function formatDecimal(value: DecimalString, options: NumberFormatOptions = {}): string {
  const { places = 2, signed = false, fallback = "—" } = options;
  if (value === null || value === undefined || value === "") return fallback;

  const parsed = parse(value);
  if (!parsed) return fallback;

  const rounded = roundFraction(parsed, places);
  const body = places > 0 ? `${group(rounded.integer)}.${rounded.fraction}` : group(rounded.integer);
  // "-0.00" is noise; treat a value that rounds to zero as zero.
  const isZero = /^0+$/.test(rounded.integer) && /^0*$/.test(rounded.fraction);
  if (rounded.negative && !isZero) return `-${body}`;
  if (signed && !isZero) return `+${body}`;
  return body;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
  CHF: "CHF ",
  CAD: "CA$",
  AUD: "A$",
};

/** Minor units per currency, mirroring the backend's `CURRENCY_MINOR_UNITS`. */
const MINOR_UNITS: Record<string, number> = { JPY: 0, KRW: 0, BTC: 8, ETH: 8 };

export function formatMoney(
  value: DecimalString,
  currency = "USD",
  options: NumberFormatOptions = {},
): string {
  const places = options.places ?? MINOR_UNITS[currency.toUpperCase()] ?? 2;
  const formatted = formatDecimal(value, { ...options, places });
  if (formatted === (options.fallback ?? "—")) return formatted;

  const symbol = CURRENCY_SYMBOLS[currency.toUpperCase()] ?? `${currency.toUpperCase()} `;
  return formatted.startsWith("-")
    ? `-${symbol}${formatted.slice(1)}`
    : formatted.startsWith("+")
      ? `+${symbol}${formatted.slice(1)}`
      : `${symbol}${formatted}`;
}

/** The API sends percentages as whole numbers: "12.5" means 12.5%. */
export function formatPercent(value: DecimalString, options: NumberFormatOptions = {}): string {
  const formatted = formatDecimal(value, { places: 2, ...options });
  return formatted === (options.fallback ?? "—") ? formatted : `${formatted}%`;
}

export function formatR(value: DecimalString, options: NumberFormatOptions = {}): string {
  const formatted = formatDecimal(value, { places: 2, signed: true, ...options });
  return formatted === (options.fallback ?? "—") ? formatted : `${formatted}R`;
}

/** Ratios (profit factor, Sharpe) — no unit suffix. */
export function formatRatio(value: DecimalString, options: NumberFormatOptions = {}): string {
  return formatDecimal(value, { places: 2, ...options });
}

export function formatQuantity(value: DecimalString): string {
  if (value === null || value === undefined) return "—";
  const parsed = parse(value);
  if (!parsed) return "—";
  // Whole share counts read better without a fraction; fractional crypto keeps its precision.
  const trimmed = parsed.fraction.replace(/0+$/, "");
  return formatDecimal(value, { places: Math.min(trimmed.length, 8) });
}

/**
 * An instrument price.
 *
 * Prices are not money amounts: a currency's minor units say nothing about how finely an
 * instrument quotes. An equity ticks in cents, a currency pair in pips, and a crypto pair can
 * carry eight decimals — and the API sends whatever precision the fill actually had, which for a
 * derived average can be a long tail. So: always show at least two decimals, keep genuine
 * precision up to six, and round the tail away rather than printing it.
 *
 * Six is a display decision, not a storage one. The value is unchanged server-side; this only
 * stops a page rendering "50953.860589764" where a trader expects a price.
 */
export function formatPrice(value: DecimalString, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const parsed = parse(value);
  if (!parsed) return fallback;
  const significant = parsed.fraction.replace(/0+$/, "").length;
  return formatDecimal(value, { places: Math.min(Math.max(significant, 2), 6), fallback });
}

export function formatInteger(value: number | null | undefined, fallback = "—"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return group(String(Math.trunc(value)));
}

/** Sign of a decimal string, without parsing it to a number. */
export function signOf(value: DecimalString): -1 | 0 | 1 {
  if (value === null || value === undefined) return 0;
  const parsed = parse(value);
  if (!parsed) return 0;
  const isZero = /^0*$/.test(parsed.integer) && /^0*$/.test(parsed.fraction);
  if (isZero) return 0;
  return parsed.negative ? -1 : 1;
}

/** Tailwind text colour for a P&L value. */
export function pnlClass(value: DecimalString): string {
  const sign = signOf(value);
  if (sign > 0) return "text-profit";
  if (sign < 0) return "text-loss";
  return "text-muted";
}

/**
 * Convert a decimal string to a JS number **for charting only**.
 * Never feed the result back into a displayed figure.
 */
export function toChartNumber(value: DecimalString): number {
  if (value === null || value === undefined) return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

// --- time --------------------------------------------------------------------

export function formatDateTime(iso: string | null | undefined, fallback = "—"): string {
  if (!iso) return fallback;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return fallback;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDate(iso: string | null | undefined, fallback = "—"): string {
  if (!iso) return fallback;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return fallback;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(date);
}

/**
 * A wall-clock time of day, from an ISO time string: "18:00:00" → "18:00".
 *
 * Deliberately not passed through `Date`: this is a time in a named market's own zone, not an
 * instant, and constructing a Date would reinterpret it in the viewer's timezone — the exact
 * mistake that made daily bars land on the wrong weekday.
 */
export function formatClock(time: string | null | undefined, fallback = "—"): string {
  if (!time) return fallback;
  const match = /^(\d{2}):(\d{2})/.exec(time.trim());
  return match ? `${match[1]}:${match[2]}` : fallback;
}

/** Human duration from seconds: "4m", "2h 15m", "3d 4h". */
export function formatDuration(seconds: number | null | undefined, fallback = "—"): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return fallback;
  // A negative hold is not a duration — it means an exit was recorded before its entry. Show the
  // "no value" dash rather than a literal "-2559312s", which reads as a real measurement.
  if (seconds < 0) return fallback;
  if (seconds < 60) return `${Math.round(seconds)}s`;

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;

  const hours = Math.floor(minutes / 60);
  const remainderMinutes = minutes % 60;
  if (hours < 24) return remainderMinutes ? `${hours}h ${remainderMinutes}m` : `${hours}h`;

  const days = Math.floor(hours / 24);
  const remainderHours = hours % 24;
  return remainderHours ? `${days}d ${remainderHours}h` : `${days}d`;
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";

  const deltaSeconds = (date.getTime() - Date.now()) / 1000;
  const absolute = Math.abs(deltaSeconds);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  if (absolute < 60) return formatter.format(Math.round(deltaSeconds), "second");
  if (absolute < 3600) return formatter.format(Math.round(deltaSeconds / 60), "minute");
  if (absolute < 86400) return formatter.format(Math.round(deltaSeconds / 3600), "hour");
  return formatter.format(Math.round(deltaSeconds / 86400), "day");
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** "new_york_am" -> "New york am"; used for enum values with no dedicated label. */
export function humanise(value: string | null | undefined, fallback = "—"): string {
  if (!value) return fallback;
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
