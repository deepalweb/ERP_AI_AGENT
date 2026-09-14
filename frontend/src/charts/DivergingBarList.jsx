import { useState } from "react";
import { formatNumber } from "../format";

export default function DivergingBarList({
  data,
  formatValue = formatNumber,
  deltaGoodWhenPositive = true,
  ariaLabel = "Ranked change chart",
}) {
  const [hoverIdx, setHoverIdx] = useState(null);

  if (!data || data.length === 0) {
    return <div className="chart-empty">No data</div>;
  }

  const maxAbs = Math.max(...data.map((d) => Math.abs(d.delta)), 1e-9);

  return (
    <div className="diverging-bar-list" role="img" aria-label={ariaLabel}>
      {data.map((d, i) => {
        const pct = Math.min((Math.abs(d.delta) / maxAbs) * 100, 100);
        const isGood = deltaGoodWhenPositive ? d.delta >= 0 : d.delta <= 0;
        const side = d.delta >= 0 ? "right" : "left";
        return (
          <div
            key={d.name}
            className={`diverging-bar-row${i === hoverIdx ? " diverging-bar-row-hover" : ""}`}
            onMouseEnter={() => setHoverIdx(i)}
            onMouseLeave={() => setHoverIdx(null)}
            tabIndex={0}
          >
            <div className="diverging-bar-label" title={d.name}>
              {d.name}
            </div>
            <div className="diverging-bar-track">
              <div className="diverging-bar-center" />
              <div
                className={`diverging-bar-fill diverging-bar-fill-${isGood ? "good" : "bad"} diverging-bar-fill-${side}`}
                style={{ width: `${Math.max(pct / 2, 1.5)}%` }}
              />
            </div>
            <div className="diverging-bar-value">
              {d.delta > 0 ? "+" : ""}
              {formatValue(d.delta)}
            </div>

            {i === hoverIdx && (
              <div className="chart-tooltip chart-tooltip-static">
                <div className="chart-tooltip-value">{formatValue(d.current)}</div>
                <div className="chart-tooltip-label">was {formatValue(d.baseline)}</div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
