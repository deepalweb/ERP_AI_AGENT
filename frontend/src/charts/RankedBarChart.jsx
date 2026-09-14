import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Plain magnitude ranking — e.g. top products by revenue. Unlike
 * DrivenBarChart, there's no baseline/good-bad direction here, just size:
 * one accent color, sorted descending, no zero reference line. */
export default function RankedBarChart({ data, nameKey = "name", valueKey = "value", formatValue = String }) {
  const rows = [...data].sort((a, b) => b[valueKey] - a[valueKey]);

  return (
    <ResponsiveContainer width="100%" height={Math.max(rows.length * 32, 120)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey={nameKey} width={160} stroke="var(--chart-ink-muted)" fontSize={12} />
        <Tooltip formatter={(v) => formatValue(v)} />
        <Bar dataKey={valueKey} fill="var(--series-1)" radius={4}>
          <LabelList dataKey={valueKey} position="right" formatter={formatValue} fontSize={12} fill="var(--text)" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
