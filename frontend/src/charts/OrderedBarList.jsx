import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Magnitude by a CATEGORY with its own natural order (a distribution band
 * like "<3 days / 3-7 days / 7-30 days", a severity tier) -- unlike
 * RankedBarChart, this never re-sorts by value, since doing so would scramble
 * the one thing that makes a distribution readable: its bucket order. */
export default function OrderedBarList({ data, nameKey = "name", valueKey = "value", formatValue = String, labelWidth = 160, rowHeight = 32 }) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(data.length * rowHeight, 120)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
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
