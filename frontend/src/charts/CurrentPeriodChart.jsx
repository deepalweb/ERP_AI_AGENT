import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatByUnit, monthLabel } from "../format";

export default function CurrentPeriodChart({ item }) {
  const confirm = item.confirm ?? {};
  if (!Number.isFinite(confirm.current)) return null;
  const history = (item.trend ?? []).filter((point) => Number.isFinite(point.value) && point.comparable !== false);
  const hasHistory = history.length > 1 && history.some((point) => point.month === confirm.period);
  const data = hasHistory
    ? history.map((point) => ({ label: monthLabel(point.month), value: point.value, current: point.month === confirm.period }))
    : [...(Number.isFinite(confirm.baseline) ? [{ label: "Baseline", value: confirm.baseline }] : []), { label: "Current", value: confirm.current, current: true }];
  const color = item.category === "critical" ? "var(--status-critical)" : item.category === "opportunity" ? "var(--status-good)" : "var(--accent)";
  const baseline = Number.isFinite(confirm.baseline) ? confirm.baseline : null;
  const values = [...data.map((point) => point.value), ...(baseline == null ? [] : [baseline])];
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  return (
    <figure className="mt-4 min-w-0" aria-label={`${item.metric.label} comparison chart`}>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-foreground-subtle">
        <span>Current <strong className="text-foreground-strong">{formatByUnit(confirm.current, confirm.unit)}</strong></span>
        <span>Baseline <strong className="text-foreground-strong">{baseline == null ? "Unavailable" : formatByUnit(baseline, confirm.unit)}</strong></span>
      </div>
      <div className="h-44 w-full min-w-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 8, bottom: 0, left: 0 }} accessibilityLayer>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="label" tick={{ fill: "var(--text-subtle)", fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={15} />
            <YAxis width={66} tickFormatter={(value) => formatByUnit(value, confirm.unit)} tick={{ fill: "var(--text-subtle)", fontSize: 10 }} axisLine={false} tickLine={false} domain={[low < 0 ? low - span * 0.1 : 0, high + span * 0.15]} />
            <Tooltip cursor={{ fill: "var(--accent)", opacity: 0.06 }} formatter={(value) => [formatByUnit(value, confirm.unit), "Measured value"]} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)" }} />
            {baseline != null && hasHistory && <ReferenceLine y={baseline} stroke="var(--text-subtle)" strokeDasharray="4 4" />}
            <ReferenceLine y={0} stroke="var(--border)" />
            <Bar dataKey="value" maxBarSize={42} isAnimationActive={false}>
              {data.map((point, index) => <Cell key={`${point.label}-${index}`} fill={point.current ? color : "var(--accent)"} fillOpacity={point.current ? 1 : 0.35} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-xs text-foreground-subtle">{hasHistory ? "Matching portions of each month, not full-month totals. Solid bar: current period. Dashed line: baseline mean." : baseline == null ? "Only the current measurement is available; no comparable baseline was returned." : "Current period against its comparable baseline. Historical points are not available."}</figcaption>
      <details className="mt-2 text-xs text-foreground-subtle">
        <summary className="cursor-pointer text-accent">Chart values</summary>
        <table className="mt-2 w-full text-left"><caption className="sr-only">{item.metric.label} plotted values</caption><thead><tr><th scope="col">Period</th><th scope="col" className="text-right">Value</th></tr></thead><tbody>{data.map((point, index) => <tr key={`${point.label}-${index}`}><th scope="row" className="py-1 font-normal">{point.label}{point.current && point.label !== "Current" ? " (current)" : ""}</th><td className="text-right tabular-nums">{formatByUnit(point.value, confirm.unit)}</td></tr>)}</tbody></table>
      </details>
    </figure>
  );
}
