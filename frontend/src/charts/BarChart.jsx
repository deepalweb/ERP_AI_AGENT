import { useState } from "react";
import { niceScale } from "./niceScale";
import { formatCompact, monthLabel } from "../format";

const VB_W = 400;
const VB_H = 200;
const PAD = { top: 10, right: 12, bottom: 22, left: 42 };
const MAX_BAR_W = 20;

function roundedTopRectPath(x, y, w, h, r) {
  const rr = Math.min(r, w / 2, Math.max(h, 0));
  if (h <= 0) return "";
  return `M${x},${y + h} L${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} L${x + w},${y + h} Z`;
}

export default function BarChart({ data, valueKey = "value", formatValue = formatCompact, ariaLabel = "Bar chart" }) {
  const [hoverIdx, setHoverIdx] = useState(null);

  if (!data || data.length === 0) {
    return <div className="chart-empty">No data</div>;
  }

  const innerW = VB_W - PAD.left - PAD.right;
  const innerH = VB_H - PAD.top - PAD.bottom;
  const maxVal = Math.max(...data.map((d) => d[valueKey]));
  const { max, ticks } = niceScale(maxVal, 5);

  const band = innerW / data.length;
  const barW = Math.min(MAX_BAR_W, band * 0.5);
  const xFor = (i) => PAD.left + band * i + band / 2 - barW / 2;
  const yFor = (v) => PAD.top + innerH - (v / max) * innerH;
  const baseline = PAD.top + innerH;

  const hovered = hoverIdx !== null ? data[hoverIdx] : null;

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${VB_W} ${VB_H}`} className="chart-svg" role="img" aria-label={ariaLabel}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={VB_W - PAD.right} y1={yFor(t)} y2={yFor(t)} className="chart-gridline" />
            <text x={PAD.left - 8} y={yFor(t)} className="chart-axis-label" textAnchor="end" dominantBaseline="middle">
              {formatCompact(t)}
            </text>
          </g>
        ))}

        {data.map((d, i) => {
          const h = baseline - yFor(d[valueKey]);
          return (
            <g key={d.month}>
              <path
                d={roundedTopRectPath(xFor(i), yFor(d[valueKey]), barW, h, 4)}
                className={`chart-bar${d.is_partial ? " chart-bar-partial" : ""}${i === hoverIdx ? " chart-bar-hover" : ""}`}
              />
              <rect
                x={PAD.left + band * i}
                y={PAD.top}
                width={band}
                height={innerH}
                fill="transparent"
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(null)}
              />
              <text x={xFor(i) + barW / 2} y={VB_H - 8} className="chart-axis-label" textAnchor="middle">
                {monthLabel(d.month)}
              </text>
            </g>
          );
        })}
      </svg>

      {hovered && (
        <div
          className="chart-tooltip"
          style={{
            left: `${((xFor(hoverIdx) + barW / 2) / VB_W) * 100}%`,
            top: `${(yFor(hovered[valueKey]) / VB_H) * 100}%`,
          }}
        >
          <div className="chart-tooltip-value">{formatValue(hovered[valueKey])}</div>
          <div className="chart-tooltip-label">
            {monthLabel(hovered.month)}
            {hovered.is_partial ? " · partial month" : ""}
          </div>
        </div>
      )}
    </div>
  );
}
