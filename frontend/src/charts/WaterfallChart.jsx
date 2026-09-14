import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** "How did we get from A to B" — each step is either a running total
 * (renders as a full bar from zero) or a delta applied to the previous
 * total (renders as a floating bar between the before/after totals, using
 * a transparent stacked base). Standard Recharts waterfall technique since
 * there's no built-in waterfall chart type.
 *
 * steps: [{ name, value, type: "total" | "delta" }] — for "delta" steps,
 * value's sign determines direction (negative = subtracted, e.g. COGS). */
function prepareWaterfallData(steps) {
  let running = 0;
  return steps.map((step) => {
    if (step.type === "total") {
      running = step.value;
      return { name: step.name, base: 0, visible: step.value, isTotal: true, raw: step.value };
    }
    const before = running;
    running += step.value;
    return {
      name: step.name,
      base: Math.min(before, running),
      visible: Math.abs(step.value),
      isTotal: false,
      raw: step.value,
    };
  });
}

export default function WaterfallChart({ steps, formatValue = String }) {
  const data = prepareWaterfallData(steps);

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--chart-gridline)" vertical={false} />
        <XAxis dataKey="name" stroke="var(--chart-ink-muted)" fontSize={12} />
        <YAxis tickFormatter={formatValue} stroke="var(--chart-ink-muted)" fontSize={12} width={72} />
        <Tooltip formatter={(_, __, item) => formatValue(item.payload.raw)} />
        <Bar dataKey="base" stackId="a" fill="transparent" />
        <Bar dataKey="visible" stackId="a" radius={4}>
          {data.map((d) => (
            <Cell key={d.name} fill={d.isTotal ? "var(--series-1)" : "var(--status-critical)"} />
          ))}
          <LabelList dataKey="raw" position="top" formatter={formatValue} fontSize={12} fill="var(--text)" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
