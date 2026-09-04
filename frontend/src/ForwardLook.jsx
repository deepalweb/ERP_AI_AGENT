import { formatCurrency, formatNumber, monthLabel } from "./format";

function ProjectionRow({ label, projection, formatValue }) {
  if (!projection) return null;

  const { based_on_month, based_on_value, recent_growth_rates_pct, projected_low, projected_high } = projection;
  const lowPct = Math.min(...recent_growth_rates_pct);
  const highPct = Math.max(...recent_growth_rates_pct);

  return (
    <div className="forward-look-row">
      <span className="forward-look-label">{label}</span>
      <span className="forward-look-value">
        {formatValue(based_on_value)} ({monthLabel(based_on_month)}) &rarr; {formatValue(projected_low)}&ndash;
        {formatValue(projected_high)} next month
      </span>
      <span className="forward-look-detail">
        recent growth: {lowPct}%&ndash;{highPct}%
      </span>
    </div>
  );
}

export default function ForwardLook({ data }) {
  if (!data || (!data.revenue && !data.orders)) return null;

  return (
    <div className="forward-look-section">
      <div className="forward-look-title">Forward Look &mdash; if the recent pace holds</div>
      <ProjectionRow label="Revenue" projection={data.revenue} formatValue={formatCurrency} />
      <ProjectionRow label="Orders" projection={data.orders} formatValue={formatNumber} />
      <p className="forward-look-note">&#9888; {data.note}</p>
    </div>
  );
}
