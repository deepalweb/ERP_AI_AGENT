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

export function monthLabel(monthKey) {
  if (!/^\d{4}-\d{2}$/.test(monthKey || "")) return monthKey;
  const [year, month] = monthKey.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, {
    month: "short",
    year: "2-digit",
  });
}
