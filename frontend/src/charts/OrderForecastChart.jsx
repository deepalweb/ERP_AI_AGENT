import { CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Actual value transitioning into the predicted value — solid through
 * today, dashed for the forecast. A "Today" reference line marks the
 * boundary. actualLabel/predictedLabel default to order-count wording
 * (every existing caller forecasts orders); pass them explicitly for a
 * non-order series (e.g. revenue) so the legend/tooltip don't mislabel it. */
export default function OrderForecastChart({
  data,
  asOfDate,
  formatOrders = String,
  formatX = String,
  actualLabel = "Current order count",
  predictedLabel = "Predicted order count",
}) {
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
          name={actualLabel}
          stroke="var(--series-1)"
          strokeWidth={2}
          dot={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="predicted_orders"
          name={predictedLabel}
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
