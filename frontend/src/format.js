export function formatCompact(value) {
  const n = Number(value) || 0;
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `${sign}${(abs / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

export function formatCurrency(value) {
  return `Rs ${formatCompact(value)}`;
}

export function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

export function formatPct(value) {
  if (value === null || value === undefined) return "n/a";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
}

export function formatPercent(value) {
  return `${Number(value || 0).toFixed(2)}%`;
}

export function formatDuration(minutes) {
  const n = Number(minutes) || 0;
  if (n < 60) return `${n.toFixed(1)} min`;
  if (n < 1440) return `${(n / 60).toFixed(1)} hrs`;
  return `${(n / 1440).toFixed(1)} days`;
}

export function formatByUnit(value, unit) {
  if (value === null || value === undefined) return "n/a";
  if (unit === "currency") return formatCurrency(value);
  if (unit === "percent") return `${Number(value).toFixed(2)}%`;
  return formatNumber(value);
}

export function formatDate(dateStr) {
  if (!dateStr) return "n/a";
  const [year, month, day] = dateStr.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

// A move's color must follow the metric's own direction (higher_is_good vs
// higher_is_bad -- see mcp_server/tools/intelligence.py's MetricDef), never
// just the raw sign of the delta: a rise in "Severely delayed delivery
// rate" (higher_is_bad) is worse, not better, so coloring it green would
// tell a manager the opposite of what's true. Direction unknown -> neutral
// rather than guessing. Shared here (not duplicated per-page) since both
// Home.jsx's compact finding cards and the Investigate case list/detail
// pages need the identical rule.
export function moveToneClass(delta, direction) {
  if (delta == null || delta === 0) return "text-foreground-subtle";
  if (direction === "higher_is_good") return delta > 0 ? "text-good" : "text-critical";
  if (direction === "higher_is_bad") return delta > 0 ? "text-critical" : "text-good";
  return "text-foreground-subtle";
}

export function monthLabel(monthKey) {
  if (!/^\d{4}-\d{2}$/.test(monthKey || "")) return monthKey;
  const [year, month] = monthKey.split("-").map(Number);
  // Full 4-digit year, not 2-digit ("Sep 2026", not "Sep 26") -- a 2-digit
  // year reads as a day-of-month at a glance (real confusion this caused:
  // "Data through Sep 26" was misread as September 26th, a future date,
  // when it meant September 2026). Never worth the few saved characters.
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, {
    month: "short",
    year: "numeric",
  });
}

/** confirm.period's shape depends entirely on the finding's horizon --
 * "YYYY-MM" for monthly, a full "YYYY-MM-DD" for a snapshot/state finding,
 * or an already-human date-range string ("Sep 03 - Sep 09") for a weekly/
 * trailing one. Regression: CaseDetail.jsx called formatDate() (which
 * requires a full "YYYY-MM-DD" and returns "Invalid Date" on anything
 * shorter) unconditionally on confirm.period, which happened to be
 * correct only for the one finding shape (snapshot) it was written
 * against -- a real revenue case (monthly, period "2026-08") rendered
 * "As of Invalid Date" in production. This auto-detects the shape instead
 * of assuming one, so every horizon renders correctly from the same call
 * site. */
export function formatConfirmPeriod(period) {
  if (!period) return "n/a";
  if (/^\d{4}-\d{2}$/.test(period)) return monthLabel(period);
  if (/^\d{4}-\d{2}-\d{2}$/.test(period)) return formatDate(period);
  return period; // already a human-readable label (e.g. a trailing-window date range)
}

// A case/finding's fallback display name when no real `title` string is
// available yet (e.g. a case with no observations) -- "gross_margin" ->
// "Gross margin". Shared by Investigate.jsx and Actions.jsx so both name
// an untitled case the same way rather than drifting into two phrasings.
export function prettifyMetricId(id) {
  if (!id) return "Untitled case";
  return id.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// state_findings.py bakes "- SKU 003193" into a product's display name on
// several finding shapes (negative_stock_control, cost_coverage_control,
// top_seller_stock) -- real duplication when the row also has its own
// structured `sku` field/column. Display-only: never touches the
// underlying data, since other consumers may still want the full string.
export function stripSkuSuffix(name) {
  if (!name) return name;
  return name.replace(/\s*-\s*SKU\s+\S+\s*$/i, "").trim();
}
