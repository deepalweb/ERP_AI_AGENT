import { useRef, useState } from "react";
import { niceScale } from "./niceScale";
import { formatCompact, formatCurrency, monthLabel } from "../format";

const VB_W = 400;
const VB_H = 200;
const PAD = { top: 10, right: 12, bottom: 22, left: 42 };

export default function LineChart({
  data,
  valueKey = "revenue",
  formatValue = formatCurrency,
  ariaLabel = "Trend line chart",
}) {
  const [hoverIdx, setHoverIdx] = useState(null);
  const svgRef = useRef(null);

  if (!data || data.length === 0) {
    return <div className="chart-empty">No data</div>;
  }

  const innerW = VB_W - PAD.left - PAD.right;
  const innerH = VB_H - PAD.top - PAD.bottom;
  const maxVal = Math.max(...data.map((d) => d[valueKey]));
  const { max, ticks } = niceScale(maxVal, 5);

  const xFor = (i) => PAD.left + (data.length === 1 ? innerW / 2 : (i / (data.length - 1)) * innerW);
  const yFor = (v) => PAD.top + innerH - (v / max) * innerH;

  const lastIdx = data.length - 1;
  const lastIsPartial = Boolean(data[lastIdx]?.is_partial);
  const solidUpTo = lastIsPartial ? lastIdx - 1 : lastIdx;

  const solidPoints = data
    .slice(0, solidUpTo + 1)
    .map((d, i) => `${xFor(i)},${yFor(d[valueKey])}`)
    .join(" ");
  const dashedPoints =
    lastIsPartial && solidUpTo >= 0
      ? `${xFor(solidUpTo)},${yFor(data[solidUpTo][valueKey])} ${xFor(lastIdx)},${yFor(data[lastIdx][valueKey])}`
      : "";
  const areaPoints = `${xFor(0)},${PAD.top + innerH} ${data
    .map((d, i) => `${xFor(i)},${yFor(d[valueKey])}`)
    .join(" ")} ${xFor(lastIdx)},${PAD.top + innerH}`;

  function handleMove(e) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * VB_W;
    let nearest = 0;
    let best = Infinity;
    data.forEach((_, i) => {
      const dx = Math.abs(xFor(i) - relX);
      if (dx < best) {
        best = dx;
        nearest = i;
      }
    });
    setHoverIdx(nearest);
  }

  const hovered = hoverIdx !== null ? data[hoverIdx] : null;

  return (
    <div className="chart-wrap">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        className="chart-svg"
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIdx(null)}
        role="img"
        aria-label={ariaLabel}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={VB_W - PAD.right} y1={yFor(t)} y2={yFor(t)} className="chart-gridline" />
            <text x={PAD.left - 8} y={yFor(t)} className="chart-axis-label" textAnchor="end" dominantBaseline="middle">
              {formatCompact(t)}
            </text>
          </g>
        ))}

        {data.map((d, i) => (
          <text key={d.month} x={xFor(i)} y={VB_H - 8} className="chart-axis-label" textAnchor="middle">
            {monthLabel(d.month)}
          </text>
        ))}

        <polygon points={areaPoints} className="chart-area" />
        <polyline points={solidPoints} className="chart-line" />
        {dashedPoints && <polyline points={dashedPoints} className="chart-line chart-line-partial" />}

        {data.map((d, i) => (
          <circle
            key={d.month}
            cx={xFor(i)}
            cy={yFor(d[valueKey])}
            r={i === hoverIdx ? 5 : 4}
            className="chart-marker"
          />
        ))}

        {hovered && (
          <line x1={xFor(hoverIdx)} x2={xFor(hoverIdx)} y1={PAD.top} y2={PAD.top + innerH} className="chart-crosshair" />
        )}
      </svg>

      {hovered && (
        <div
          className="chart-tooltip"
          style={{
            left: `${(xFor(hoverIdx) / VB_W) * 100}%`,
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
