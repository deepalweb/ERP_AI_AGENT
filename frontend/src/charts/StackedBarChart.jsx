/** A single 100%-width composition bar -- "what share of the whole is each
 * category," read at a glance with no legend-matching required (each segment
 * is labeled by direct proportion, not just a color key). Plain HTML/CSS,
 * not Recharts: one proportional bar has no axis, tooltip, or data points to
 * justify a charting library's overhead -- see dataviz skill guidance on
 * when a chart is just a styled div.
 *
 * segments: [{ name, value, color }] -- color is a CSS color (a token var),
 * never assigned by index, since each segment here has a fixed meaning
 * (Healthy/Overstock/Dead stock) that must always read the same color. */
export default function StackedBarChart({ segments, formatValue = String, height = 28 }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  if (!total) return <p className="text-sm text-foreground/50">No data.</p>;

  return (
    <div>
      <div className="flex w-full overflow-hidden rounded-lg" style={{ height }} role="img" aria-label="Composition bar">
        {segments.map((s) => {
          const pct = (s.value / total) * 100;
          if (pct <= 0) return null;
          return (
            <div
              key={s.name}
              className="flex items-center justify-center text-[11px] font-medium text-white/90"
              style={{ width: `${pct}%`, backgroundColor: s.color }}
              title={`${s.name}: ${formatValue(s.value)} (${pct.toFixed(0)}%)`}
            >
              {pct >= 8 ? `${pct.toFixed(0)}%` : ""}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs">
        {segments.map((s) => (
          <div key={s.name} className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 flex-shrink-0 rounded-sm" style={{ backgroundColor: s.color }} />
            <span className="font-medium text-foreground-strong">{s.name}</span>
            <span className="text-foreground/50">
              {formatValue(s.value)} ({((s.value / total) * 100).toFixed(0)}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
