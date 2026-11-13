const SYMBOLS: Record<string, string> = {
  USD: "$", SGD: "S$", HKD: "HK$", EUR: "€", GBP: "£", JPY: "¥", CNY: "¥", KRW: "₩",
};

const missing = (v: unknown): v is null | undefined =>
  v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v));

export const DASH = "—";

export function num(v: number | null | undefined, digits = 2): string {
  if (missing(v)) return DASH;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function money(v: number | null | undefined, currency = "USD", digits = 2): string {
  if (missing(v)) return DASH;
  const sign = v < 0 ? "−" : "";
  return `${sign}${SYMBOLS[currency] ?? currency + " "}${num(Math.abs(v), digits)}`;
}

export function compact(v: number | null | undefined, currency?: string): string {
  if (missing(v)) return DASH;
  const abs = Math.abs(v);
  const [div, unit] = abs >= 1e12 ? [1e12, "T"] : abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"]
    : abs >= 1e3 ? [1e3, "K"] : [1, ""];
  const body = `${num(abs / div, div === 1 ? 0 : 1)}${unit}`;
  return `${v < 0 ? "−" : ""}${currency ? SYMBOLS[currency] ?? "" : ""}${body}`;
}

/** A value already in percent units. */
export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (missing(v)) return DASH;
  const sign = v < 0 ? "−" : signed && v > 0 ? "+" : "";
  return `${sign}${num(Math.abs(v), digits)}%`;
}

/** A fraction (0.62) shown as a percentage. */
export const frac = (v: number | null | undefined, digits = 1, signed = false) =>
  missing(v) ? DASH : pct(v * 100, digits, signed);

export function sigma(v: number | null | undefined): string {
  if (missing(v)) return DASH;
  return `${v < 0 ? "−" : "+"}${num(Math.abs(v), 2)}σ`;
}

export const direction = (v: number | null | undefined) =>
  missing(v) || v === 0 ? "flat" : v > 0 ? "up" : "down";

export function shortDate(iso: string): string {
  return new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "2-digit",
  });
}

export function ago(hours: number | null): string {
  if (hours === null) return "";
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
