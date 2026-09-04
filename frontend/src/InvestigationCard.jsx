import { formatNumber } from "./format";

export const CATEGORY_META = {
  critical: { emoji: "🔴", label: "Critical" },
  opportunity: { emoji: "🟢", label: "Opportunity" },
};

export const PRIORITY_META = {
  high: { label: "High priority", className: "priority-high" },
  medium: { label: "Medium priority", className: "priority-medium" },
  low: { label: "Low priority", className: "priority-low" },
};

/** Split a contributor's value into a headline figure + a dimmer "was X" qualifier. */
export function contributorValueParts(contributor, mode) {
  if (mode === "rate") {
    return { main: `${contributor.current}%`, sub: `(was ${contributor.baseline}%, n=${contributor.current_volume})` };
  }
  if (mode === "composition") {
    return { main: `${contributor.current_share_pct}% of cases`, sub: `(was ${contributor.baseline_share_pct}%)` };
  }
  const sign = contributor.delta >= 0 ? "+" : "";
  return { main: `${sign}${formatNumber(contributor.delta)}`, sub: null };
}
