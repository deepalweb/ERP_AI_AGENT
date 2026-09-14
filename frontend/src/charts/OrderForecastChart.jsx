import { CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Actual order count transitioning into the predicted order count — solid
 * through today, dashed for the forecast. A "Today" reference line marks
 * the boundary. */
export default function OrderForecastChart({ data, asOfDate, formatOrders = String, formatX = String }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--chart-gridline)" vertical={false} />
        <XAxis dataKey="date" tickFormatter={formatX} stroke="var(--chart-ink-muted)" fontSize={12} />
        <YAxis tickFormatter={formatOrders} stroke="var(--chart-ink-muted)" fontSize={12} width={56} />
        <Tooltip labelFormatter={formatX} formatter={(v) => (v == null ? null : formatOrders(v))} />
        {asOfDate && (
          <ReferenceLine
            x={asOfDate}
            stroke="var(--chart-ink-muted)"
            strokeDasharray="3 3"
            label={{ value: "Today", position: "top", fontSize: 11, fill: "var(--chart-ink-muted)" }}
          />
        )}
        <Line
          type="monotone"
          dataKey="actual_orders"
          name="Current order count"
          stroke="var(--series-1)"
          strokeWidth={2}
          dot={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="predicted_orders"
          name="Predicted order count"
          stroke="var(--series-1)"
          strokeWidth={2}
          strokeDasharray="6 4"
          dot={false}
          connectNulls={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
