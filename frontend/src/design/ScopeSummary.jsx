import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import { DOMAIN_META } from "./domains";
import { formatNumber } from "../format";
import { ExposureByDomain } from "./InvestigationOverview";
import InventoryFamilies from "./InventoryFamilies";
import { BANDS } from "./InvestigationDomains";
import { newThisWeek, scopeTotals } from "./scopeMath";

const BAND_COLORS = Object.fromEntries(BANDS.map((band) => [band.key, band.color]));

const plural = (n, word) => `${formatNumber(n)} ${word}${n === 1 ? "" : "s"}`;

function Tile({ label, value, onClick, tone }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="min-w-0 rounded-xl border border-border bg-surface p-3 text-left transition hover:bg-surface-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
    >
      <p className="text-[11px] font-medium uppercase tracking-wider text-foreground-subtle">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${tone ?? "text-foreground-strong"}`}>{formatNumber(value)}</p>
    </button>
  );
}

/** Scope tiles and a status-mix donut. Tiles and donut slices set filters on the issue table. */
export function ScopeTiles({ rows, impacts, domain, onFilter }) {
  const totals = scopeTotals(rows, domain);
  const fresh = newThisWeek(impacts, domain);
  const mix = [
    { key: "urgent", name: "Critical / high", value: totals.urgent, filter: { status: "active", priority: "critical" } },
    { key: "other", name: "Other active", value: totals.other, filter: { status: "active", priority: "" } },
    { key: "opportunity", name: "Opportunities", value: totals.opportunity, filter: { status: "active", priority: "" } },
    { key: "verification", name: "Awaiting verification", value: totals.verification, filter: { status: "verification_pending", priority: "" } },
  ].filter((slice) => slice.value > 0);

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="grid grid-cols-2 gap-2">
        <Tile label="Open cases" value={totals.active} onClick={() => onFilter({ status: "active", priority: "" })} />
        <Tile label="Critical / high" value={totals.urgent} tone="text-critical" onClick={() => onFilter({ status: "active", priority: "critical" })} />
        <Tile label="Verifying" value={totals.verification} onClick={() => onFilter({ status: "verification_pending", priority: "" })} />
        <Tile label="New this week" value={fresh} onClick={() => onFilter({ status: "active", priority: "", sort: "newest" })} />
      </div>
      <div className="flex items-center gap-3">
        {mix.length === 0 ? (
          <p className="text-sm text-foreground-subtle">No open cases in this scope.</p>
        ) : (
          <>
            <div className="relative h-36 w-36 flex-none" aria-hidden="true">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={mix} dataKey="value" innerRadius="62%" outerRadius="100%" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
                    {mix.map((slice) => <Cell key={slice.key} fill={BAND_COLORS[slice.key]} />)}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-xl font-semibold tabular-nums text-foreground-strong">{formatNumber(totals.active + totals.verification)}</span>
                <span className="text-[10px] uppercase tracking-wider text-foreground-subtle">in view</span>
              </div>
            </div>
            <ul className="min-w-0 space-y-1.5 text-xs">
              {mix.map((slice) => (
                <li key={slice.key}>
                  <button type="button" onClick={() => onFilter(slice.filter)} className="inline-flex items-center gap-2 rounded px-1 py-0.5 text-left text-foreground hover:bg-surface-sunken">
                    <span className="inline-block h-2.5 w-2.5 flex-none rounded-sm" style={{ background: BAND_COLORS[slice.key] }} />
                    <span>{slice.name}</span>
                    <span className="font-semibold tabular-nums">{formatNumber(slice.value)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

/** Needs attention: the chips that point at work to do, each setting a filter on the table below. */
export function ScopeAttention({ rows, impacts, domain, onFilter }) {
  const totals = scopeTotals(rows, domain);
  const unvalued = (impacts ?? []).filter(
    (row) => (!domain || row.domain === domain) && Number.isFinite(row.impact?.financial_impact_rs) && (row.impact?.valued_units_pct ?? 100) < 50,
  ).length;
  const attention = [];
  if (totals.urgent) attention.push({ key: "urgent", tone: "critical", text: `${plural(totals.urgent, "critical / high case")} need action`, filter: { status: "active", priority: "critical" } });
  if (totals.verification) attention.push({ key: "verify", tone: "warning", text: `${plural(totals.verification, "case")} awaiting verification`, filter: { status: "verification_pending", priority: "" } });
  if (unvalued) attention.push({ key: "unvalued", tone: "warning", text: `${plural(unvalued, "rupee estimate")} rest on under half their units`, filter: null });
  const toneClass = { critical: "border-critical/25 bg-critical/10 text-critical", warning: "border-warning/25 bg-warning/10 text-warning" };

  return (
    <section aria-label="Needs attention">
      <h2 className="text-sm font-semibold text-foreground-strong">Needs attention</h2>
      {attention.length === 0 ? (
        <p className="mt-2 text-sm text-foreground-subtle">Nothing in this scope needs action right now.</p>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          {attention.map((item) => (
            item.filter ? (
              <button
                key={item.key}
                type="button"
                onClick={() => onFilter(item.filter)}
                className={`rounded-full border px-3 py-1 text-xs font-medium hover:opacity-80 ${toneClass[item.tone]}`}
              >
                {item.text}
              </button>
            ) : (
              <span key={item.key} className={`rounded-full border px-3 py-1 text-xs font-medium ${toneClass[item.tone]}`}>{item.text}</span>
            )
          ))}
        </div>
      )}
    </section>
  );
}

/** Management overview. All domains: cases by area. Inventory: the family cards are the navigation. */
export function ManagementOverview({ rows, impacts, domain, families, familyKey, onFilter }) {
  if (domain === "") return null;
  if (domain === "inventory") {
    return (
      <section aria-label="Inventory overview" className="space-y-4">
        <h2 className="text-sm font-semibold text-foreground-strong">Inventory overview</h2>
        <p className="text-xs text-foreground-subtle">Select a management family to filter the issue list to its findings.</p>
        <InventoryFamilies
          families={families}
          selectedKey={familyKey}
          onSelect={(key) => onFilter({ familyKey: familyKey === key ? "" : key })}
        />
        <ExposureByDomain impacts={impacts} />
      </section>
    );
  }
  const label = DOMAIN_META[domain]?.label ?? domain;
  return (
    <section aria-label={`${label} overview`} className="space-y-2">
      <h2 className="text-sm font-semibold text-foreground-strong">{label} overview</h2>
      <p className="text-sm text-foreground-subtle">No family grouping for this area yet. Use the issue list below.</p>
    </section>
  );
}
