import { useState } from "react";
import RankedBarChart from "../charts/RankedBarChart";
import { formatNumber } from "../format";
import { AffectedRecordTable } from "./CaseVisuals";

/** One warehouse-transfer discrepancy issue for one reason (mcp_server.tools.
 * transfer_discrepancy). Shows the outlet and product breakdowns as charts,
 * an outlet table with each outlet's rate, then the detail lines. Gated by
 * the caller on metric_id starting with "transfer_discrepancy_". */

const DETAIL_PREVIEW_ROWS = 25;
const CHART_PRODUCT_ROWS = 10;

const OUTLET_COLUMNS = [
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "lines", label: "Lines" },
  { key: "units", label: "Units", format: (v) => formatNumber(v) },
  { key: "days", label: "Days" },
  { key: "unit_rate", label: "Unit rate", format: (v) => (v == null ? "n/a" : `${v}%`) },
  { key: "value_rs", label: "Est. value", format: (v) => (v ? `Rs ${formatNumber(v)}` : "n/a") },
];

const DETAIL_COLUMNS = [
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "product", label: "Product", format: (v, row) => (
    <span>
      {shortProduct(v)}
      {row.sku && <span className="block text-xs text-foreground-subtle">SKU {row.sku}</span>}
    </span>
  ) },
  { key: "lines", label: "Lines" },
  { key: "units", label: "Units", format: (v) => formatNumber(v) },
  { key: "days", label: "Days" },
  { key: "last_seen", label: "Last seen" },
  { key: "value_rs", label: "Est. value", format: (v) => (v ? `Rs ${formatNumber(v)}` : "n/a") },
];

function shortOutlet(name) {
  return (name ?? "").replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() || name;
}

function shortProduct(name) {
  return (name ?? "").split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
}

const EXCLUDED_COLUMNS = [
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  { key: "lines", label: "Lines" },
  { key: "units", label: "Units", format: (v) => formatNumber(v) },
  { key: "states", label: "Receipt status", format: (v) => v.join(", ") },
];

function summarySentence(latest) {
  const outlets = latest.outlet_rows ?? [];
  const units = latest.impact?.units_affected ?? 0;
  const lines = latest.impact?.count ?? 0;
  const reason = (latest.reason ?? "").toLowerCase();
  const top = [...outlets].sort((a, b) => (b.unit_rate ?? 0) - (a.unit_rate ?? 0))[0];
  const parts = [
    `${formatNumber(lines)} received-receipt ${reason} lines (${formatNumber(units)} units) across ${outlets.length} outlet${outlets.length === 1 ? "" : "s"} in the 14-day window ending ${latest.confirm?.period ?? "the latest data"}.`,
  ];
  if (top?.unit_rate != null) {
    parts.push(`${shortOutlet(top.outlet)} has the highest rate: ${top.unit_rate}% of the units it received.`);
  }
  return parts.join(" ");
}

export default function TransferDiscrepancyInvestigationPanel({ latest }) {
  const outletRows = latest?.outlet_rows ?? [];
  const excludedRows = latest?.excluded_rows ?? [];
  const allDetailRows = latest?.transfer_rows ?? [];
  const [showAllDetails, setShowAllDetails] = useState(false);
  const detailRows = showAllDetails ? allDetailRows : allDetailRows.slice(0, DETAIL_PREVIEW_ROWS);
  const breakdowns = latest?.breakdowns ?? [];
  const checks = latest?.root_cause?.checks ?? [];
  if (outletRows.length === 0) return null;

  const outletBreakdown = (breakdowns.find((b) => b.dimension === "outlet")?.top_contributors ?? [])
    .map((c) => ({ ...c, name: shortOutlet(c.name) }));
  const allProductBreakdown = (breakdowns.find((b) => b.dimension === "product")?.top_contributors ?? [])
    .map((c) => ({ ...c, name: shortProduct(c.name) }));
  const productBreakdown = allProductBreakdown.slice(0, CHART_PRODUCT_ROWS);

  return (
    <div className="space-y-6">
      <p className="text-sm text-foreground">{summarySentence(latest)}</p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Units by outlet</h4>
          <RankedBarChart data={outletBreakdown} nameKey="name" valueKey="current" formatValue={(v) => `${formatNumber(v)} units`} />
        </div>
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Units by product</h4>
          <RankedBarChart data={productBreakdown} nameKey="name" valueKey="current" formatValue={(v) => `${formatNumber(v)} units`} labelWidth={260} rowHeight={36} />
          {allProductBreakdown.length > CHART_PRODUCT_ROWS && (
            <p className="mt-2 text-xs text-foreground-subtle">Top {CHART_PRODUCT_ROWS} of {allProductBreakdown.length} products shown.</p>
          )}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">By outlet</h4>
        <AffectedRecordTable columns={OUTLET_COLUMNS} rows={outletRows} rowKey="outlet" />
        {latest.impact?.lines_total != null && (
          <p className="mt-2 text-xs text-foreground-subtle">
            {(latest.impact.valued_units_pct ?? 0) < 50
              ? `Most affected units (${formatNumber(100 - (latest.impact.valued_units_pct ?? 0))}%) have no usable catalog cost, so the rupee figure is not reliable. Rely on the unit count.`
              : `Estimated exposure at current catalog cost. ${latest.impact.valued_units_pct ?? 0}% of affected units `
                + `(${formatNumber(latest.impact.lines_priced ?? 0)} of ${formatNumber(latest.impact.lines_total)} lines) had usable cost; the total is a lower bound.`}
          </p>
        )}
      </div>

      {excludedRows.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
            Excluded from the figures above
          </h4>
          <p className="mb-2 text-xs text-foreground-subtle">
            Lines on cancelled or not-yet-received receipts. They may not represent missing goods; confirm before counting them.
          </p>
          <AffectedRecordTable columns={EXCLUDED_COLUMNS} rows={excludedRows} rowKey="outlet" />
        </div>
      )}

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Details by product</h4>
        <AffectedRecordTable columns={DETAIL_COLUMNS} rows={detailRows} rowKey="id" />
        {allDetailRows.length > DETAIL_PREVIEW_ROWS && (
          <button
            type="button"
            className="mt-2 text-sm text-accent hover:underline"
            onClick={() => setShowAllDetails((v) => !v)}
          >
            {showAllDetails ? "Show top rows only" : `Show all ${allDetailRows.length} product rows`}
          </button>
        )}
      </div>

      {checks.length > 0 && (
        <ul className="space-y-2">
          {checks.map((check) => (
            <li key={check.source} className="text-sm text-foreground">
              <span className="font-medium text-foreground-strong">{check.verdict.replace(/_/g, " ")}:</span>{" "}
              {check.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
