import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Bar + line on dual axes — e.g. revenue (bars) against order count (line)
 * over the same periods. Modeled on the reference screenshot's "Revenue &
 * Orders" combo chart, not its data — this app's natural grain is monthly,
 * not hourly, so it plots whatever period series it's given. */
export default function ComboChart({ data, xKey, barKey, lineKey, formatBar = String, formatLine = String, formatX = String }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--chart-gridline)" vertical={false} />
        <XAxis dataKey={xKey} tickFormatter={formatX} stroke="var(--chart-ink-muted)" fontSize={12} />
        <YAxis yAxisId="bar" tickFormatter={formatBar} stroke="var(--chart-ink-muted)" fontSize={12} width={64} />
        <YAxis yAxisId="line" orientation="right" tickFormatter={formatLine} stroke="var(--chart-ink-muted)" fontSize={12} width={56} />
        <Tooltip
          formatter={(value, name) => (name === barKey ? formatBar(value) : formatLine(value))}
          labelFormatter={formatX}
        />
        <Bar yAxisId="bar" dataKey={barKey} fill="var(--series-1)" radius={[4, 4, 0, 0]} />
        <Line yAxisId="line" type="monotone" dataKey={lineKey} stroke="var(--series-2)" strokeWidth={2} dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
