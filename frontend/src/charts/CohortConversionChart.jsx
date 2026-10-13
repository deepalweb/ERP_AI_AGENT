import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Second-order conversion by acquisition cohort, one line per window
 * (30/60/90-day) -- three series in a FIXED order (shortest window first),
 * reusing the app's existing categorical tokens (--series-1/2/3), the same
 * ones GroupedStackedBarChart/TrendChart already draw from, rather than
 * inventing a new palette for one chart. A null point (not enough time
 * passed yet, or too few customers for a reliable rate) breaks the line
 * via `connectNulls={false}` -- bridging it would imply a real reading
 * that doesn't exist yet. */
const WINDOW_SERIES = [
  { key: "30", label: "30-day repeat", color: "var(--series-1)" },
  { key: "60", label: "60-day repeat", color: "var(--series-2)" },
  { key: "90", label: "90-day repeat", color: "var(--series-3)" },
];

export default function CohortConversionChart({ cohorts, formatX = String, height = 240 }) {
  const data = cohorts.map((c) => ({
    month: c.month,
    ...Object.fromEntries(WINDOW_SERIES.map(({ key }) => [key, c.windows[key]?.rate_pct ?? null])),
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--chart-gridline)" vertical={false} />
        <XAxis dataKey="month" tickFormatter={formatX} stroke="var(--chart-ink-muted)" fontSize={12} />
        <YAxis tickFormatter={(v) => `${v}%`} stroke="var(--chart-ink-muted)" fontSize={12} width={48} />
        <Tooltip formatter={(v) => (v == null ? "Not yet available" : `${v}%`)} labelFormatter={formatX} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {WINDOW_SERIES.map(({ key, label, color }) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            name={label}
            stroke={color}
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
