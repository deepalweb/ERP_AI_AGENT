import { formatPct } from "../format";

const SPARK_W = 60;
const SPARK_H = 20;

function Sparkline({ trend }) {
  if (!trend || trend.length < 2) return null;
  const min = Math.min(...trend);
  const max = Math.max(...trend);
  const range = max - min || 1;
  const xFor = (i) => (i / (trend.length - 1)) * SPARK_W;
  const yFor = (v) => SPARK_H - ((v - min) / range) * SPARK_H;
  const points = trend.map((v, i) => `${xFor(i)},${yFor(v)}`).join(" ");

  return (
    <svg className="stat-tile-spark" viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} aria-hidden="true">
      <polyline points={points} />
    </svg>
  );
}

export default function StatTile({ label, value, delta, deltaGoodWhenPositive = true, status, note, trend }) {
  const hasDelta = delta !== undefined && delta !== null;
  const deltaIsGood = hasDelta && (deltaGoodWhenPositive ? delta >= 0 : delta <= 0);

  return (
    <div className={`stat-tile${status ? ` stat-tile-${status}` : ""}`}>
      <div className="stat-tile-top">
        <div className="stat-tile-text">
          <span className="stat-tile-label">{label}</span>
          <span className="stat-tile-value">{value}</span>
        </div>
        <Sparkline trend={trend} />
      </div>
      {hasDelta && (
        <span className={`stat-tile-delta ${deltaIsGood ? "stat-tile-delta-good" : "stat-tile-delta-bad"}`}>
          {formatPct(delta)} vs prior month
        </span>
      )}
      {note && <span className="stat-tile-note">{note}</span>}
    </div>
  );
}
