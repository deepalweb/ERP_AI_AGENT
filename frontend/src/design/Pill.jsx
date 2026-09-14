const TONE_CLASS = {
  good: "bg-good/15 text-good",
  warning: "bg-warning/15 text-warning",
  critical: "bg-critical/15 text-critical",
};

/** A small colored badge for a table cell value that needs to jump out as
 * an outlier without the reader scanning every row — e.g. a store's
 * cancellation rate. Pass `tone` directly, or use `pillTone()` below for
 * the common "green under a threshold, amber under a second, red above"
 * pattern. */
export default function Pill({ children, tone = "good" }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${TONE_CLASS[tone] ?? TONE_CLASS.good}`}>
      {children}
    </span>
  );
}

export function pillTone(value, { warning, critical }) {
  if (value >= critical) return "critical";
  if (value >= warning) return "warning";
  return "good";
}
