import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Plain magnitude ranking — e.g. top products by revenue. Unlike
 * DrivenBarChart, there's no baseline/good-bad direction here, just size:
 * one accent color, sorted descending, no zero reference line.
 *
 * labelWidth/rowHeight default to the original 160/32 (every existing
 * caller is unaffected) -- added 2026-10-01 because long category names
 * (real product names run well past 160px) wrapped onto 2-3 lines inside a
 * 32px-tall row and visually overlapped the row above/below it, reading as
 * "concatenated text" rather than a ranked list. A caller with long labels
 * (transfer_discrepancy's real product names) passes larger values instead
 * of this component guessing a size from the data. */
export default function RankedBarChart({ data, nameKey = "name", valueKey = "value", formatValue = String, labelWidth = 160, rowHeight = 32 }) {
  const rows = [...data].sort((a, b) => b[valueKey] - a[valueKey]);

  return (
    <ResponsiveContainer width="100%" height={Math.max(rows.length * rowHeight, 120)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey={nameKey} width={labelWidth} stroke="var(--chart-ink-muted)" fontSize={12} />
        <Tooltip formatter={(v) => formatValue(v)} />
        <Bar dataKey={valueKey} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={2} radius={[0, 4, 4, 0]}>
          <LabelList dataKey={valueKey} position="right" formatter={formatValue} fontSize={12} fill="var(--text)" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
