const VALUE_TONE_CLASS = {
  neutral: "text-foreground-strong",
  warning: "text-warning",
  critical: "text-critical",
};

/** A single static "at a glance" number. A flagged tile colors its VALUE
 * text, not the whole tile's border/background — a full colored box makes
 * an otherwise-ordinary-looking grid of numbers visually break rhythm and
 * scream, where a colored number is enough to register as notable without
 * that. (Revised from an earlier colored-box version per explicit design
 * feedback — applies everywhere this component is used, including Home.) */
export default function KpiTile({ icon, value, label, sub, tone = "neutral" }) {
  return (
    <div className="rounded-xl border border-border p-4">
      <div className="flex items-center gap-2">
        {icon && <span className="text-lg" aria-hidden="true">{icon}</span>}
        <span className={`text-xl font-bold ${VALUE_TONE_CLASS[tone] ?? VALUE_TONE_CLASS.neutral}`}>{value}</span>
      </div>
      <div className="mt-1 text-xs font-medium uppercase tracking-wide text-foreground/60">{label}</div>
      {sub && <div className="mt-0.5 text-xs text-foreground/50">{sub}</div>}
    </div>
  );
}
