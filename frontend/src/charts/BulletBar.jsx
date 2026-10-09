const TONE_FILL = { critical: "var(--status-critical)", warning: "var(--status-warning)", good: "var(--status-good)" };

/** A single value against a scale, with a threshold marked as a vertical
 * line -- "how close is this number to the line that matters," read without
 * a legend. Built for days-of-cover (the 3-day low-stock line), but generic
 * to any single-value-vs-threshold case. Plain div/CSS, same reasoning as
 * StackedBarChart: one bar with one reference line doesn't need an axis or
 * a charting library. */
export default function BulletBar({ value, max, threshold, tone = "good", height = 10 }) {
  const safeMax = max > 0 ? max : 1;
  const fillPct = Math.max(0, Math.min(100, (value / safeMax) * 100));
  const thresholdPct = Math.max(0, Math.min(100, (threshold / safeMax) * 100));

  return (
    <div className="relative w-full rounded-full bg-surface-sunken" style={{ height }} role="img" aria-label={`${value} against a threshold of ${threshold}`}>
      <div
        className="h-full rounded-full"
        style={{ width: `${fillPct}%`, backgroundColor: TONE_FILL[tone] ?? TONE_FILL.good }}
      />
      <div
        className="absolute top-0 h-full w-px bg-foreground/60"
        style={{ left: `${thresholdPct}%` }}
        title={`Threshold: ${threshold}`}
      />
    </div>
  );
}
