import { Bar, BarChart, Cell, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Ranked, diverging bar chart for "what's driving this change" — the
 * Recharts-based replacement for charts/DivergingBarList.jsx's hand-rolled
 * SVG version. Bars are colored by whether the move is good or bad for the
 * metric (deltaGoodWhenPositive flips which side is "good"), not by sign
 * alone. */
export default function DrivenBarChart({ data, deltaGoodWhenPositive = true, formatValue = String }) {
  const rows = [...data].sort((a, b) => b.delta - a.delta);

  return (
    <ResponsiveContainer width="100%" height={Math.max(rows.length * 36, 120)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" width={140} stroke="var(--chart-ink-muted)" fontSize={12} />
        <ReferenceLine x={0} stroke="var(--chart-baseline)" />
        <Tooltip formatter={(v) => formatValue(v)} />
        <Bar dataKey="delta" radius={4}>
          {rows.map((row) => {
            const isGood = deltaGoodWhenPositive ? row.delta >= 0 : row.delta <= 0;
            return <Cell key={row.name} fill={isGood ? "var(--status-good)" : "var(--status-critical)"} />;
          })}
          <LabelList dataKey="delta" position="right" formatter={formatValue} fontSize={12} fill="var(--text)" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
