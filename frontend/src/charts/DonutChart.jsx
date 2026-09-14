import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

const COLORS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--status-good)",
  "var(--status-warning)",
  "var(--accent)",
  "var(--status-critical)",
  "var(--status-serious)",
];

/** Composition as a donut — e.g. revenue by department. Legend lists every
 * slice by name since a wedge alone can't label a long tail of small
 * categories legibly. Set showLegend={false} when the caller renders its
 * own richer legend alongside the chart (e.g. with extra columns like
 * revenue) instead of this built-in name-only one. Pass `colors` to
 * override the default palette when the slices have a real meaning worth
 * matching (e.g. a "problem" slice that should read red, not whatever
 * color happens to fall at its index). */
export default function DonutChart({
  data,
  nameKey = "name",
  valueKey = "value",
  formatValue = String,
  showLegend = true,
  colors = COLORS,
}) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie data={data} dataKey={valueKey} nameKey={nameKey} innerRadius={60} outerRadius={90} paddingAngle={1}>
          {data.map((d, i) => (
            <Cell key={d[nameKey]} fill={colors[i % colors.length]} />
          ))}
        </Pie>
        <Tooltip formatter={(v) => formatValue(v)} />
        {showLegend && <Legend layout="vertical" align="right" verticalAlign="middle" wrapperStyle={{ fontSize: 12 }} />}
      </PieChart>
    </ResponsiveContainer>
  );
}
