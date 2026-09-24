const STATUS_STROKE = { critical: "var(--status-critical)", warning: "var(--status-warning)", good: "var(--status-good)" };

/** A 0-100 score as a radial arc instead of a bare number -- used for
 * Business Health. Deliberately has no benchmark/target marker: the
 * backend's health score has no real "target" concept behind it (only a
 * value and a status), so a fixed benchmark arc would be a fabricated
 * reference point, not real data. */
export default function RadialGauge({ value, status = "good", size = 112, strokeWidth = 9, sublabel }) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const dash = (pct / 100) * circumference;

  return (
    <div className="relative flex-none" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--surface-sunken)" strokeWidth={strokeWidth} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={STATUS_STROKE[status] ?? STATUS_STROKE.good}
          strokeWidth={strokeWidth}
          strokeDasharray={`${dash} ${circumference}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <span className="font-display text-[26px] font-bold tabular-nums leading-none text-foreground-strong">{Math.round(value ?? 0)}</span>
        {sublabel && <span className="mt-0.5 text-[10px] text-foreground-subtle">{sublabel}</span>}
      </div>
    </div>
  );
}
