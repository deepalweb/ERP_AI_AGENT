import { CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

/** Trend line, with an optional forecast tail: pass `forecast` as
 * [{ x, low, high }] and each point renders as a dashed continuation plus a
 * shaded low/high band, so "what's likely next" reads as part of the same
 * chart instead of a separate number on another page (the plan's Forecast
 * section). Solid = actual, dashed = projected.
 *
 * `compact` renders a true sparkline (no grid/axes/tooltip, small fixed
 * height) for inline use in a summary row or ranked list — the collapsed
 * finding card and the Business Impact section both need "prove the trend
 * at a glance" without the full chart's chrome. */
export default function TrendChart({
  data,
  xKey = "month",
  valueKey = "value",
  forecast,
  formatValue = String,
  formatX = String,
  compact = false,
  height = 220,
  stroke = "var(--series-1)",
  gridStroke = "var(--chart-gridline)",
  axisStroke = "var(--chart-ink-muted)",
}) {
  const merged = [
    ...data.map((d) => ({ [xKey]: d[xKey], actual: d[valueKey] })),
    ...(forecast ?? []).map((f) => ({ [xKey]: f[xKey], forecastLow: f.low, forecastHigh: f.high, forecast: (f.low + f.high) / 2 })),
  ];

  if (compact) {
    return (
      <ResponsiveContainer width="100%" height={40}>
        <ComposedChart data={merged} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <Line type="monotone" dataKey="actual" stroke={stroke} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          {forecast && (
            <Line type="monotone" dataKey="forecast" stroke={stroke} strokeWidth={2} strokeDasharray="4 3" dot={false} connectNulls isAnimationActive={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={merged} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={gridStroke} vertical={false} />
        <XAxis dataKey={xKey} tickFormatter={formatX} stroke={axisStroke} fontSize={12} />
        <YAxis tickFormatter={formatValue} stroke={axisStroke} fontSize={12} width={64} />
        <Tooltip formatter={(v) => formatValue(v)} labelFormatter={formatX} />
        <Line type="monotone" dataKey="actual" stroke={stroke} strokeWidth={2} dot={false} connectNulls />
        {forecast && (
          <Line type="monotone" dataKey="forecast" stroke={stroke} strokeWidth={2} strokeDasharray="6 4" dot={false} connectNulls />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
