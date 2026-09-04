import { useState } from "react";
import { formatCurrency } from "../format";

export default function HBarList({
  data,
  valueKey = "revenue",
  formatValue = formatCurrency,
  detailKey,
  detailLabel,
  ariaLabel = "Ranked bar chart",
}) {
  const [hoverIdx, setHoverIdx] = useState(null);

  if (!data || data.length === 0) {
    return <div className="chart-empty">No data</div>;
  }

  const maxVal = Math.max(...data.map((d) => d[valueKey]));

  return (
    <div className="hbar-list" role="img" aria-label={ariaLabel}>
      {data.map((d, i) => {
        const pct = maxVal > 0 ? (d[valueKey] / maxVal) * 100 : 0;
        return (
          <div
            key={d.name}
            className={`hbar-row${i === hoverIdx ? " hbar-row-hover" : ""}`}
            onMouseEnter={() => setHoverIdx(i)}
            onMouseLeave={() => setHoverIdx(null)}
            tabIndex={0}
          >
            <div className="hbar-label" title={d.name}>
              {d.name}
            </div>
            <div className="hbar-track">
              <div className="hbar-fill" style={{ width: `${Math.max(pct, 2)}%` }} />
            </div>
            <div className="hbar-value">{formatValue(d[valueKey])}</div>

            {i === hoverIdx && detailKey && (
              <div className="chart-tooltip chart-tooltip-static">
                <div className="chart-tooltip-value">{formatValue(d[valueKey])}</div>
                <div className="chart-tooltip-label">
                  {d[detailKey]?.toLocaleString()} {detailLabel}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
