const ET = "America/New_York";

export const money = (v: number | null | undefined, sign = false) =>
  v == null
    ? "—"
    : (sign && v > 0 ? "+" : "") + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const pct = (v: number | null | undefined, digits = 2, sign = false) =>
  v == null ? "—" : (sign && v > 0 ? "+" : "") + (v * 100).toFixed(digits) + "%";

export const num = (v: number | null | undefined, digits = 2, sign = false) =>
  v == null ? "—" : (sign && v > 0 ? "+" : "") + v.toFixed(digits);

/** Exchange time (ET) for an epoch-seconds timestamp. */
export const et = (ts: number | null | undefined, withDate = false) =>
  ts == null
    ? "—"
    : new Date(ts * 1000).toLocaleString("en-GB", {
        timeZone: ET,
        hour12: false,
        ...(withDate ? { year: "numeric", month: "2-digit", day: "2-digit" } : {}),
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });

/** Local wall-clock time for an epoch-seconds timestamp. */
export const wall = (ts: number | null | undefined) =>
  ts == null ? "—" : new Date(ts * 1000).toLocaleString("en-GB", { hour12: false });

export const short = (h?: string | null) => (h || "").slice(0, 10);

export const cls = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");

export const pnlClass = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");
