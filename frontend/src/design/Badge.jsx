const TONE = {
  critical: { text: "text-critical", bg: "bg-critical/10", border: "border-critical/25", dot: "bg-critical" },
  warning: { text: "text-warning", bg: "bg-warning/10", border: "border-warning/25", dot: "bg-warning" },
  good: { text: "text-good", bg: "bg-good/10", border: "border-good/25", dot: "bg-good" },
  accent: { text: "text-accent", bg: "bg-accent-soft", border: "border-accent-border", dot: "bg-accent" },
  neutral: { text: "text-foreground", bg: "bg-surface-sunken", border: "border-border", dot: "bg-foreground-subtle" },
};

/** The one tone-driven badge primitive -- replaces three separate
 * implementations of the same "priority/severity/status" concept
 * (design/Pill.jsx's table-cell pill, SignalCard's PriorityBadge, and
 * SignalCard's dot-only SeverityTag). `variant="pill"` (default) is a
 * filled rounded-full badge for a table cell or standalone tag;
 * `variant="dot"` is a small colored dot + label for a denser inline
 * context (a finding card's severity line) where a full pill background
 * would be too heavy repeated many times on one screen. */
export default function Badge({ tone = "neutral", variant = "pill", children, className = "" }) {
  const t = TONE[tone] ?? TONE.neutral;
  if (variant === "dot") {
    return (
      <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${t.text} ${className}`}>
        <span className={`h-1.5 w-1.5 flex-none rounded-full ${t.dot}`} aria-hidden="true" />
        {children}
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${t.bg} ${t.text} ${t.border} ${className}`}
    >
      {children}
    </span>
  );
}

/** priority -> tone mapping shared by every caller that has a finding's
 * `priority` field (critical/high/medium/worth_pursuing -- see
 * mcp_server/tools/investigation.py's _priority_for). Matches Home's own
 * 4-way severity palette (critical=rose, high=amber, medium=cyan,
 * positive=emerald) -- medium used to share "warning" (amber) with high,
 * losing the distinction Home already draws between them. */
export function toneForPriority(priority) {
  if (priority === "critical") return "critical";
  if (priority === "high") return "warning";
  if (priority === "medium") return "accent";
  if (priority === "worth_pursuing") return "good";
  return "accent";
}
