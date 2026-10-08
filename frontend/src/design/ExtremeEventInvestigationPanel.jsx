import RankedBarChart from "../charts/RankedBarChart";
import { formatNumber } from "../format";
import { AffectedRecordTable } from "./CaseVisuals";

/** One case for the whole large-Missing-Item issue (mcp_server.tools.
 * extreme_event): a plain-language summary, outlet and product charts, then
 * the transfer lines in a table. Gated by the caller on metric_id starting
 * with "extreme_event_". */

function shortOutlet(name) {
  return (name ?? "").replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() || name;
}

function shortProduct(name) {
  return (name ?? "").split(/\s+-\s+(?:Celeste\b|SKU\b)/)[0];
}

const COLUMNS = [
  { key: "date", label: "Date" },
  { key: "outlet", label: "Outlet", format: (v) => shortOutlet(v) },
  {
    key: "product", label: "Product",
    format: (v, row) => (
      <span>
        {shortProduct(v)}
        {row.sku && <span className="block text-xs text-foreground-subtle">SKU {row.sku}</span>}
      </span>
    ),
  },
  { key: "receipt", label: "Receipt" },
  { key: "receipt_status", label: "Receipt status" },
  { key: "expected_qty", label: "Expected", format: (v) => formatNumber(v) },
  { key: "received_qty", label: "Received", format: (v) => formatNumber(v) },
  { key: "recorded_qty", label: "Recorded shortage", format: (v) => formatNumber(v) },
  { key: "vs_usual", label: "Vs usual" },
  { key: "recorded_by", label: "Recorded by", format: (v) => personLabel(v) },
  { key: "flags", label: "Flags", format: (v) => <FlagList flags={v} /> },
];

function personLabel(login) {
  if (!login) return "Not recorded";
  if (!login.includes("@")) return `${login} (shared account)`;
  return login;
}

function summarySentence(rows, breakdowns) {
  const outlets = breakdowns.find((b) => b.dimension === "outlet")?.top_contributors ?? [];
  const units = rows.reduce((sum, r) => sum + r.recorded_qty, 0);
  const cancelled = rows.filter((r) => r.receipt_status === "cancel").length;
  const parts = [
    `${rows.length} transfer ${rows.length === 1 ? "line" : "lines"} recorded an unusually large Missing Item shortage `
      + `(${formatNumber(units)} units) across ${outlets.length} outlet${outlets.length === 1 ? "" : "s"}.`,
  ];
  if (cancelled) {
    parts.push(`${cancelled} of ${rows.length} are on receipts that Odoo shows as cancelled, so confirm the goods arrived before treating them as losses.`);
  }
  return parts.join(" ");
}

const FLAG_LABELS = {
  "Receipt cancelled in Odoo": "Cancelled receipt",
  "Quantity does not match expected minus received": "Qty mismatch",
};

function FlagList({ flags }) {
  if (!flags?.length) return <span className="text-foreground-subtle">None</span>;
  return <span>{flags.map((f) => FLAG_LABELS[f] ?? f).join(", ")}</span>;
}

export default function ExtremeEventInvestigationPanel({ latest }) {
  const rows = latest?.extreme_rows ?? [];
  const breakdowns = latest?.breakdowns ?? [];
  const checks = latest?.root_cause?.checks ?? [];
  if (rows.length === 0) return null;

  const outletBreakdown = (breakdowns.find((b) => b.dimension === "outlet")?.top_contributors ?? [])
    .map((c) => ({ ...c, name: shortOutlet(c.name) }));
  const productBreakdown = (breakdowns.find((b) => b.dimension === "product")?.top_contributors ?? [])
    .map((c) => ({ ...c, name: shortProduct(c.name) }));
  return (
    <div className="space-y-6">
      <p className="text-sm text-foreground">{summarySentence(rows, breakdowns)}</p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Units by outlet</h4>
          <RankedBarChart
            data={outletBreakdown}
            nameKey="name"
            valueKey="current"
            formatValue={(v) => `${formatNumber(v)} units`}
          />
        </div>
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Units by product</h4>
          <RankedBarChart
            data={productBreakdown}
            nameKey="name"
            valueKey="current"
            formatValue={(v) => `${formatNumber(v)} units`}
            labelWidth={260}
            rowHeight={36}
          />
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Transfer lines</h4>
        <AffectedRecordTable columns={COLUMNS} rows={rows} rowKey="id" />
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
