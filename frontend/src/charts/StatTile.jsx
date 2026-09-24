import { formatPct } from "../format";

const SPARK_W = 60;
const SPARK_H = 20;

const STATUS_TEXT_CLASS = { good: "text-good", warning: "text-warning", critical: "text-critical" };

function Sparkline({ trend }) {
  if (!trend || trend.length < 2) return null;
  const min = Math.min(...trend);
  const max = Math.max(...trend);
  const range = max - min || 1;
  const xFor = (i) => (i / (trend.length - 1)) * SPARK_W;
  const yFor = (v) => SPARK_H - ((v - min) / range) * SPARK_H;
  const points = trend.map((v, i) => `${xFor(i)},${yFor(v)}`).join(" ");

  return (
    <svg width={SPARK_W} height={SPARK_H} viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} className="flex-none text-accent" aria-hidden="true">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

/** A single stat tile with an inline sparkline -- fixed this pass (Phase 1
 * of the frontend redesign) to render on real Tailwind classes: it
 * previously emitted `stat-tile*` class names with no backing CSS
 * anywhere in the repo, so it rendered completely unstyled wherever it
 * was used. */
export default function StatTile({ label, value, delta, deltaGoodWhenPositive = true, status, note, trend }) {
  const hasDelta = delta !== undefined && delta !== null;
  const deltaIsGood = hasDelta && (deltaGoodWhenPositive ? delta >= 0 : delta <= 0);

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-wide text-foreground-subtle">{label}</div>
          <div className={`mt-1 text-xl font-semibold tabular-nums ${STATUS_TEXT_CLASS[status] ?? "text-foreground-strong"}`}>{value}</div>
        </div>
        <Sparkline trend={trend} />
      </div>
      {hasDelta && (
        <span className={`mt-2 inline-block text-xs font-medium tabular-nums ${deltaIsGood ? "text-good" : "text-critical"}`}>
          {formatPct(delta)} vs prior month
        </span>
      )}
      {note && <div className="mt-1 text-xs text-foreground-subtle">{note}</div>}
    </div>
  );
}
