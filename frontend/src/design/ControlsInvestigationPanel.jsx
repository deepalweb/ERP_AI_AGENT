import RankedBarChart from "../charts/RankedBarChart";
import { MetricSummaryCard } from "./CaseVisuals";
import { formatCurrency, formatDate, formatNumber, stripSkuSuffix as displayName } from "../format";

/** Frontend Intelligence Integration, Phase 2: Business Data Controls
 * investigation panel. Gated by the caller on domain === "controls" --
 * renders one of two shapes depending on which control fired
 * (metric_id), since negative-stock and cost-coverage findings carry
 * genuinely different fields.
 *
 * Every number here traces to a real field on the case's own finding
 * snapshot (state_findings._negative_stock_control_finding /
 * _cost_coverage_control_finding) -- nothing computed client-side. */
export default function ControlsInvestigationPanel({ latest, statusLabel }) {
  if (!latest) return null;
  if (latest.metric_id === "negative_stock_control") return <NegativeStockPanel latest={latest} statusLabel={statusLabel} />;
  if (latest.metric_id === "cost_coverage_control") return <CostCoveragePanel latest={latest} statusLabel={statusLabel} />;
  return null;
}

// Real check for THIS finding's own product breakdown -- source is always
// "{dimension}_concentration" (evidence_engine.py's own naming), so this
// reads whatever the check actually found (concentrated or not) rather
// than asserting a fixed claim that would be wrong on a future case where
// the affected records ARE concentrated in one segment.
function concentrationCheckFor(latest, dimension) {
  return (latest.root_cause?.checks ?? []).find((c) => c.source === `${dimension}_concentration`) ?? null;
}

/** Established / Not established as two bullet lists, not the earlier
 * generic paragraph pair plus a redundant "possible consequence" column
 * (now its own "What this affects" block below, since that's a distinct
 * question from what the evidence has/hasn't established). */
function EstablishedNote({ established, notEstablished }) {
  return (
    <div className="mt-4 grid grid-cols-1 gap-4 border-t border-border pt-3 sm:grid-cols-2">
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Established</span>
        <ul className="mt-1.5 list-inside list-disc space-y-1 text-sm text-foreground">
          {established.map((line) => <li key={line}>{line}</li>)}
        </ul>
      </div>
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Not established</span>
        <ul className="mt-1.5 list-inside list-disc space-y-1 text-sm text-foreground-subtle">
          {notEstablished.map((line) => <li key={line}>{line}</li>)}
        </ul>
      </div>
    </div>
  );
}

/** What downstream calculations are affected by this control gap -- a
 * structural fact about the KIND of control (missing cost vs. negative
 * stock), same for every case of that kind, not fabricated per-case. */
function WhatThisAffects({ items }) {
  return (
    <div className="mt-4 border-t border-border pt-3">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">What this affects</span>
      <dl className="mt-2 space-y-2">
        {items.map((it) => (
          <div key={it.label}>
            <dt className="text-sm font-medium text-foreground-strong">{it.label}</dt>
            <dd className="text-sm text-foreground-subtle">{it.detail}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function NegativeStockPanel({ latest, statusLabel }) {
  const products = latest.products ?? [];
  const byOutlet = latest.by_outlet ?? [];
  if (products.length === 0) return null;
  const unvalued = products.filter((p) => p.value_rs == null).length;
  const productConcentration = concentrationCheckFor(latest, "product");
  const storeConcentration = concentrationCheckFor(latest, "store");

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricSummaryCard label="Affected products" value={formatNumber(products.length)} />
        <MetricSummaryCard
          label="Recorded stock value involved"
          value={formatCurrency(latest.impact?.financial_impact_rs)}
          caption={unvalued > 0 ? `${unvalued} of ${products.length} have no recorded cost -- value unknown for those` : undefined}
        />
        <MetricSummaryCard label="Data through" value={formatDate(latest.detected)} />
        <MetricSummaryCard label="Case status" value={statusLabel ?? "—"} />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {byOutlet.length > 0 && (
          <div>
            <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Affected outlets</span>
            <p className="mt-1 text-xs text-foreground-subtle">
              Counted per outlet -- a product negative at more than one outlet is counted at each, so these figures
              don&rsquo;t sum to the {products.length} company-wide total above.
            </p>
            <div className="mt-2">
              <RankedBarChart data={byOutlet} nameKey="outlet" valueKey="product_count" formatValue={formatNumber} />
            </div>
          </div>
        )}
        {products.some((p) => p.value_rs != null) && (
          <div>
            <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
              Highest-value products, by recorded stock value
            </span>
            <p className="mt-1 text-xs text-foreground-subtle">
              Same records as the table below, ranked visually by the Rs value involved.
            </p>
            <div className="mt-2">
              <RankedBarChart
                data={products.filter((p) => p.value_rs != null).slice(0, 10).map((p) => ({ ...p, name: displayName(p.name) }))}
                nameKey="name"
                valueKey="value_rs"
                formatValue={formatCurrency}
              />
            </div>
          </div>
        )}
      </div>

      <div className="mt-4 overflow-x-auto">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
          Highest-value affected products
        </span>
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="text-left text-foreground-subtle">
              <th className="pb-2 pr-3 font-medium">Product</th>
              <th className="pb-2 pr-3 font-medium">SKU</th>
              <th className="pb-2 pr-3 font-medium">Recorded qty</th>
              <th className="pb-2 font-medium">Estimated value</th>
            </tr>
          </thead>
          <tbody>
            {products.slice(0, 15).map((p) => (
              <tr key={p.sku ?? p.name} className="border-t border-border">
                <td className="py-1.5 pr-3 text-foreground-strong">{displayName(p.name)}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground-subtle">{p.sku ?? "n/a"}</td>
                <td className="py-1.5 pr-3 tabular-nums text-foreground">{formatNumber(p.qty)}</td>
                <td className="py-1.5 tabular-nums text-foreground">
                  {p.value_rs != null ? formatCurrency(p.value_rs) : "Unknown (no recorded cost)"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <WhatThisAffects
        items={[
          { label: "Stock availability", detail: "Recorded on-hand for these products may not reflect what's physically available." },
          { label: "Inventory valuation", detail: "Valuation involving these specific records may be under- or overstated until reconciled." },
        ]}
      />

      <EstablishedNote
        established={[
          `${formatNumber(products.length)} active product${products.length === 1 ? "" : "s"} have negative recorded stock company-wide.`,
          productConcentration?.detail,
          storeConcentration?.detail,
        ].filter(Boolean)}
        notEstablished={[
          "Why these specific products became negative (a late goods receipt, a POS/sync timing gap, and a genuine unrecorded loss all produce the same symptom).",
          "Whether this represents a confirmed loss, theft, or shrinkage.",
        ]}
      />
    </div>
  );
}

function CostCoveragePanel({ latest, statusLabel }) {
  const products = latest.products ?? [];
  if (products.length === 0) return null;
  const noCost = products.length;
  const total = latest.total_products ?? null;
  const coveragePct = latest.coverage_pct ?? null;
  const productConcentration = concentrationCheckFor(latest, "product");

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricSummaryCard label="Products affected" value={formatNumber(noCost)} caption={total ? `of ${formatNumber(total)} active inventory products` : undefined} />
        <MetricSummaryCard label="Cost coverage" value={coveragePct != null ? `${coveragePct}%` : "—"} />
        {/* De-emphasized (valueClassName) relative to the other three cards
            -- this is a data-quality/control exception, not a financial-loss
            case, so "Not established" shouldn't visually compete with the
            real affected-count and coverage numbers. */}
        <MetricSummaryCard
          label="Financial loss"
          value="Not established"
          caption="A record count, not a priced loss"
          valueClassName="text-base font-semibold text-foreground-subtle"
        />
        <MetricSummaryCard label="Case status" value={statusLabel ?? "—"} />
      </div>

      {/* One coverage visual, not two (a bar plus a donut) -- 7 missing out
          of 7,218 is too small a slice for a donut to communicate anything;
          it mostly reads as one solid green circle. The bar alone already
          carries the fact. */}
      {coveragePct != null && total != null && (
        <div className="mt-4 border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Coverage</span>
          <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-surface-sunken">
            <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${Math.min(100, coveragePct)}%` }} />
          </div>
          <p className="mt-2 text-xs text-foreground-subtle">
            {formatNumber(total - noCost)} of {formatNumber(total)} active inventory products have a usable cost;
            {" "}{formatNumber(noCost)} do not.
          </p>
        </div>
      )}

      <div className="mt-4 overflow-x-auto">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Products with no recorded cost</span>
        <table className="mt-2 w-full text-sm">
          <thead>
            <tr className="text-left text-foreground-subtle">
              <th className="pb-2 pr-3 font-medium">Product</th>
              <th className="pb-2 font-medium">SKU</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <tr key={p.sku ?? p.name} className="border-t border-border">
                <td className="py-1.5 pr-3 text-foreground-strong">{displayName(p.name)}</td>
                <td className="py-1.5 tabular-nums text-foreground-subtle">{p.sku ?? "n/a"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <WhatThisAffects
        items={[
          { label: "Inventory valuation", detail: "These products cannot be fully valued using the normal cost basis." },
          { label: "Margin analysis", detail: "Sales involving these products may be excluded or estimated, depending on the calculation path." },
          {
            label: "Management reporting",
            detail: `Totals remain mostly covered (${coveragePct != null ? `${coveragePct}%` : "nearly complete"}). `
              + `${total != null ? `The other ${formatNumber(total - noCost)} products have usable cost values; ` : ""}`
              + `aggregate totals can still be incomplete where these ${formatNumber(noCost)} products are involved.`,
          },
        ]}
      />

      <EstablishedNote
        established={[
          `${formatNumber(noCost)} active product${noCost === 1 ? "" : "s"} have no usable recorded cost.`,
          coveragePct != null ? `Overall cost coverage is ${coveragePct}%.` : null,
          productConcentration?.detail,
        ].filter(Boolean)}
        notEstablished={[
          "Why the cost values are missing.",
          "Whether the missing costs caused any financial loss.",
          "What the correct replacement cost should be for each of these products.",
        ]}
      />
    </div>
  );
}
