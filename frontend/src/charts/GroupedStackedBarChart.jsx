import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** One stacked bar per category, segments colored by series -- "is this
 * category dominated by one segment or several," which a set of separate
 * per-category donuts can't show side by side. series supplies both the
 * stacking order and a fixed color per segment, so the same reason/series
 * always reads the same color across every row.
 *
 * data: [{ name: <category>, [series[i].key]: <value>, ... }] */
export default function GroupedStackedBarChart({ data, series, formatValue = String, rowHeight = 40 }) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(data.length * rowHeight + 40, 160)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" width={100} stroke="var(--chart-ink-muted)" fontSize={12} />
        <Tooltip formatter={(v, key) => [formatValue(v), series.find((s) => s.key === key)?.label ?? key]} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} stackId="a" fill={s.color} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
