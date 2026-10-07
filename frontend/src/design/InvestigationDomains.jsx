import { formatNumber } from "../format";
import { domainBreakdown, scopeTotals } from "./scopeMath";

export const BANDS = [
  { key: "urgent", label: "Critical / high", color: "var(--status-critical)" },
  { key: "other", label: "Other active", color: "var(--status-warning)" },
  { key: "opportunity", label: "Opportunities", color: "var(--status-good)" },
  { key: "verification", label: "Awaiting verification", color: "var(--series-1)" },
];

/** Business areas as one row each: a stacked bar of the case mix, scaled to the largest
 * area, with the active count at the right. Each row is a button, so the list works with
 * the keyboard and the bars carry the numbers in their titles. */
export default function InvestigationDomains({ rows, selected, onSelect, loading, error, onRetry }) {
  if (loading) return <p role="status" className="text-sm text-foreground-subtle">Loading business areas…</p>;
  if (error || !rows) {
    return (
      <div role="alert" className="text-sm text-warning">
        Business-area summary unavailable.{" "}
        <button type="button" className="underline" onClick={onRetry}>Retry</button>
      </div>
    );
  }
  const areas = domainBreakdown(rows).map((band) => ({ id: band.domain, label: band.label, ...band }));
  const scale = Math.max(1, ...areas.map((a) => a.active + a.verification));
  const all = scopeTotals(rows, "");
  return (
    <div className="space-y-2">
      <button
        type="button"
        aria-pressed={selected === ""}
        onClick={() => onSelect("")}
        className={`flex w-full flex-wrap items-baseline justify-between gap-2 rounded-lg px-3 py-2 text-left hover:bg-surface-sunken ${selected === "" ? "bg-accent-soft" : ""}`}
      >
        <span className="text-sm font-semibold text-foreground-strong">All domains</span>
        <span className="text-xs text-foreground-subtle">
          <span className="text-base font-semibold tabular-nums text-foreground-strong">{formatNumber(all.active)}</span> open · <span className="text-critical">{formatNumber(all.urgent)} critical / high</span> · {formatNumber(all.verification)} verifying
        </span>
      </button>
      {areas.map((area) => {
        const pressed = selected === area.id;
        return (
          <button
            key={area.id}
            type="button"
            aria-pressed={pressed}
            onClick={() => onSelect(area.id)}
            className={`grid w-full grid-cols-[minmax(0,12rem)_minmax(0,1fr)_2.5rem] items-center gap-3 rounded-lg px-3 py-1.5 text-left hover:bg-surface-sunken ${pressed ? "bg-accent-soft" : ""}`}
          >
            <span className="truncate text-xs text-foreground-strong sm:text-sm" title={area.label}>{area.label}</span>
            <span className="flex h-3 overflow-hidden rounded-sm bg-background" aria-hidden="true">
              {BANDS.map((band) => {
                const count = area[band.key];
                return count ? (
                  <span key={band.key} title={`${count} ${band.label.toLowerCase()}`} style={{ width: `${(count / scale) * 100}%`, background: band.color }} />
                ) : null;
              })}
            </span>
            <span className="text-right text-sm font-semibold tabular-nums text-foreground-strong">{formatNumber(area.active)}</span>
          </button>
        );
      })}
      <div className="flex flex-wrap gap-x-4 gap-y-1 pt-2 text-xs text-foreground-subtle">
        {BANDS.map((band) => (
          <span key={band.key} className="inline-flex items-center gap-2"><span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: band.color }} />{band.label}</span>
        ))}
      </div>
      <p className="text-xs text-foreground-subtle">Bars share one scale. Counts are cases, not affected orders or financial loss.</p>
    </div>
  );
}
