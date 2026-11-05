import { Link } from "react-router-dom";
import Badge, { toneForPriority } from "./Badge";
import { caseStatusMeta } from "./caseStatus";
import { ARCHETYPE_LABELS, resolveFindingTemplate } from "./findingTemplates";
import { findingTypeFor } from "./inventoryDisplay";
import { inventoryDomainMeta, inventoryFamilyMeta, observationWindowLabel } from "./inventoryTaxonomy";
import { formatNumber, prettifyMetricId } from "../format";

const TEMPLATES = {
  warehouse_transfer_shortages: { question: "Which transfers were recorded short, how often does it recur, and which lines need review first?", action: "Start with the recorded shortage lines. Use the large-line and dispatcher/product findings to prioritize reconciliation of dispatch notes, planned quantities, and receipt records.", limit: "The large-line and dispatcher/product findings are overlapping views of shortage records, not additional shortage quantities. Do not add their unit totals to the broader receiving-shortage total. A recorded mismatch does not establish whether the gap occurred at dispatch, in transit, or at receiving." },
  warehouse_transfer_overages: { question: "Which receipts exceed the expected quantity?", action: "Compare planned, sent, expected, and received quantities on the listed lines; resolve unit or duplicate-record issues before stock adjustments.", limit: "Recorded overages do not establish why quantities differ or whether inventory was physically verified." },
  warehouse_dispatch_overage: { question: "Which outbound lines exceed the plan?", action: "Confirm the plan, outbound record, and authorization for each qualifying line.", limit: "A dispatch above plan is a record comparison and does not by itself establish loss or intent." },
  stock_availability: { question: "Which products are recorded as unavailable, and how current is that view?", action: "Verify current stock position and source freshness, then prioritize products with active demand.", limit: "Recorded on-hand availability does not confirm sellable quantity; unknown freshness must not be treated as current." },
  stock_count_controls: { question: "Which counts remain incomplete, and what variances were recorded after completion?", action: "Complete unfinished counts first, then reconcile completed variances separately. Review duplicate-linked findings before treating their evidence as independent.", limit: "Unscanned value is not a measured variance. Completed count measures use only the denominator fields available in each saved observation." },
  damage_and_writeoffs: { question: "What damage was recorded during transfers, and what damage was written off?", action: "Review transfer damage against receiving inspection notes; review write-off transactions separately and verify their valuation coverage.", limit: "Transfer damage records do not prove that a write-off occurred. A write-off does not establish where or why the damage happened. Estimated value may be a lower bound when cost coverage is incomplete." },
  inventory_health: { question: "Which inventory is recorded as dead or overstocked, and what value may be tied up?", action: "Confirm on-hand quantities and recent demand, then review whether affected stock can be sold, returned, or written off.", limit: "Recorded inventory value is not confirmed loss; verify current quantity, freshness, and valuation basis before acting." },
  inventory_opportunities: { question: "Which inventory outcomes improved, and what benefit is estimated?", action: "Confirm improvement persists and validate estimated benefit against subsequent outcomes before crediting savings.", limit: "Estimated recovered orders or value are not verified savings and do not prove the intervention caused the change." },
};

const FAMILY_KPIS = {
  warehouse_transfer_shortages: [["Shortage findings", (c) => c.length], ["Evidence count (per finding)", (c) => sumImpact(c, "count")], ["Latest observation", (c) => latestDate(c)]],
  warehouse_transfer_overages: [["Overage cases", (c) => c.length], ["Affected units", (c) => sumImpact(c, "units_affected")], ["Latest observation", (c) => latestDate(c)]],
  warehouse_dispatch_overage: [["Dispatch cases", (c) => c.length], ["Affected units", (c) => sumImpact(c, "units_affected")], ["Latest observation", (c) => latestDate(c)]],
  stock_availability: [["Availability findings", (c) => c.length], ["Out-of-stock rows", (c) => sumImpact(c, "products_out_of_stock")], ["Source freshness", (c) => freshnessLabel(c)]],
  stock_count_controls: [["Count findings", (c) => c.length], ["Unfinished sessions", (c) => sumImpact(c, "sessions")], ["Latest observation", (c) => latestDate(c)]],
  damage_and_writeoffs: [["Damage/write-off findings", (c) => c.length], ["Recorded measures", (c) => "Per finding (" + formatNumber(c.length) + ")"], ["Recorded or estimated value", (c) => c.length === 1 ? sumMoney(c) : "Per finding (" + formatNumber(c.length) + " available)"]],
  inventory_health: [["Inventory health findings", (c) => c.length], ["Affected products", (c) => sumImpact(c, "distinct_products")], ["Recorded value", (c) => sumMoney(c)]],
  inventory_opportunities: [["Opportunity findings", (c) => c.length], ["Current measure", (c) => firstMeasure(c, "current")], ["Estimated benefit", (c) => sumMoney(c)]],
};

function valueOf(child, key) {
  const latest = child.detail?.latest ?? {};
  return latest.impact?.[key] ?? latest.confirm?.[key] ?? child.impact_summary?.[key] ?? null;
}
function sumImpact(children, key) {
  const values = children.map((c) => valueOf(c, key)).filter((v) => Number.isFinite(Number(v)));
  if (!values.length) return "Not recorded";
  return values.length === 1 ? formatNumber(values[0]) : `Per finding (${formatNumber(values.length)} recorded)`;
}
function sumMoney(children) {
  const values = children.map((c) => valueOf(c, "financial_impact_rs") ?? valueOf(c, "value_rs")).filter((v) => Number.isFinite(Number(v)));
  if (!values.length) return "Not quantified";
  return values.length === 1 ? `Rs ${formatNumber(values[0])}*` : `Per finding (${formatNumber(values.length)} valued)*`;
}
function latestDate(children) {
  const values = children.map((c) => c.last_seen_at).filter(Boolean).sort();
  return values.length ? new Date(values.at(-1)).toLocaleDateString() : "Not recorded";
}
function freshnessLabel(children) {
  const values = children.map((c) => c.detail?.measurement?.freshness).filter(Boolean);
  return values.length && values.every((v) => v !== "unknown") ? values.join(", ") : "Unknown / not recorded";
}
function firstMeasure(children, key) {
  const c = children.find((row) => valueOf(row, key) != null);
  return c ? formatNumber(valueOf(c, key)) : "Not recorded";
}
function fmt(value) { return value == null || value === "" ? "Not recorded" : typeof value === "number" ? formatNumber(value) : String(value); }
function evidenceRows(child) {
  const latest = child.detail?.latest ?? {};
  const impact = latest.impact ?? {};
  for (const key of ["trace_rows", "extreme_rows", "pattern_rows", "dispatch_rows", "sessions", "top_products", "by_outlet", "breakdowns", "trend"]) {
    if (Array.isArray(impact[key]) && impact[key].length) return [key, impact[key]];
    if (Array.isArray(latest[key]) && latest[key].length) return [key, latest[key]];
  }
  return [null, []];
}
const COLUMN_LABELS = { outlet: "Outlet", product: "Product", name: "Name", id: "Reference", date: "Date", out_sent_on: "Dispatch date", completed_at: "Completed", plan_qty: "Planned", sent_qty: "Sent", expected_qty: "Expected at receipt", received_qty: "Received", missing_qty: "Shortage recorded", over_qty: "Over plan", qty: "Units", units: "Units", value_rs: "Recorded value", net_variance_value_rs: "Net variance", gross_abs_variance_value_rs: "Gross variance", unscanned_lines: "Unscanned lines", days_stuck: "Days open", lines: "Lines", orders: "Orders", ratio: "Rate ratio", state: "State", scanned_lines: "Scanned lines", total_lines: "Total lines", variance_rate_pct: "Variance rate", dispatcher: "Dispatcher", rate_pct: "Pair shortage rate", own_rate_pct: "Rate on other products", expected_units: "Units handled", missing_units: "Shortage units recorded", short_lines: "Short lines", other_products_expected_units: "Other-product units handled", other_products_missing_units: "Other-product shortages recorded", physical_gap_units: "Sent minus received", plan_units: "Planned units", sent_units: "Sent units", line_received_units: "Received units", reason: "Recorded reason", reason_type: "Reason type", recorded_by: "Recorded by", recorded_on: "Recorded on", transfer_out: "Transfer note", receipt: "Receipt" };
function EvidenceTable({ child }) {
  const [source, rows] = evidenceRows(child);
  const columns = rows.length ? Object.keys(rows[0]).filter((key) => COLUMN_LABELS[key]).slice(0, 7) : [];
  return <div className="mt-3 overflow-x-auto rounded-lg border border-border">
    <div className="border-b border-border px-3 py-2 text-xs font-medium text-foreground-subtle">{source ? `${prettifyMetricId(source)} · ${rows.length} records` : "Evidence rows are not included in this saved finding"}</div>
    {columns.length > 0 && <table className="w-full text-left text-xs"><thead><tr>{columns.map((key) => <th key={key} className="px-3 py-2 font-medium text-foreground-subtle">{COLUMN_LABELS[key]}</th>)}</tr></thead><tbody>{rows.slice(0, 8).map((row, i) => <tr key={i} className="border-t border-border">{columns.map((key) => <td key={key} className="px-3 py-2 text-foreground">{fmt(row[key])}</td>)}</tr>)}</tbody></table>}
    {rows.length > 8 && <p className="px-3 py-2 text-xs text-foreground-subtle">Showing 8 of {formatNumber(rows.length)} records. Open the finding for full evidence.</p>}
  </div>;
}

function ChildEvidence({ family, rows }) {
  if (!rows.length) return <p className="text-sm text-foreground-subtle">No findings match the current filters.</p>;
  return <div className="space-y-3">{rows.map((child) => {
    const latest = child.detail?.latest;
    const template = resolveFindingTemplate(child);
    const status = caseStatusMeta(child.status);
    return <article key={child.case_id} className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><Link to={`/investigate/${encodeURIComponent(child.case_id)}`} className="font-semibold text-accent hover:underline">{child.title || child.metric_id}</Link><p className="mt-1 text-xs text-foreground-subtle">{findingTypeFor(child)} · {ARCHETYPE_LABELS[template.archetype]} · {observationWindowLabel({ ...child, ...latest, metric_id: child.metric_id, impact: latest?.impact, confirm: latest?.confirm })} · observed {child.last_seen_at ? new Date(child.last_seen_at).toLocaleDateString() : "date not recorded"}</p></div><div className="flex gap-2">{child.priority && <Badge tone={toneForPriority(child.priority)}>{child.priority}</Badge>}{status && <Badge tone={status.tone}>{status.label}</Badge>}</div></div>
      {latest && <div className="mt-3 grid gap-2 sm:grid-cols-3">{Object.entries(latest.impact ?? {}).filter(([, v]) => typeof v === "number" && !Array.isArray(v)).slice(0, 3).map(([key, value]) => <div key={key} className="rounded-lg bg-surface-sunken px-3 py-2"><div className="text-xs text-foreground-subtle">{prettifyMetricId(key)}</div><div className="mt-1 text-sm font-semibold text-foreground-strong">{fmt(value)}</div></div>)}</div>}
      {family.key === "stock_count_controls" && child.related_evidence?.some((r) => r.relationship_type === "duplicate_of") && <p className="mt-2 text-xs text-warning">This finding has a duplicate-linked representation. Review the linked case before counting its measures independently.</p>}
      <EvidenceTable child={child} />
      {!child.detail && <p className="mt-2 text-xs text-foreground-subtle">Loading detailed evidence…</p>}
    </article>;
  })}</div>;
}

export default function InventoryFindingFamilyPage({ family, children = [], loading = false }) {
  if (!family) return null;
  const copy = TEMPLATES[family.key] ?? { question: "What do the findings in this family show?", action: "Open each investigation and review its evidence before deciding on action.", limit: "No dedicated family interpretation is configured; the findings remain available individually." };
  const familyMeta = inventoryFamilyMeta(family.key);
  const domainMeta = inventoryDomainMeta(family.domain_key ?? familyMeta?.domainKey);
  const kpis = FAMILY_KPIS[family.key] ?? [["Active findings", (c) => formatNumber(c.length)], ["Latest observation", (c) => latestDate(c)]];
  const observed = latestDate(children);
  const detailChildren = children.filter((c) => c.detail?.measurement);
  const unknownFreshness = detailChildren.filter((c) => !c.detail.measurement.freshness || c.detail.measurement.freshness === "unknown").length;
  return <section aria-label={`${family.label} investigation`} className="space-y-5">
    <header className="rounded-2xl border border-border bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-wider text-accent">Inventory finding family{domainMeta ? " · " + domainMeta.label : ""}</p><h2 className="mt-1 font-display text-2xl font-semibold text-foreground-strong">{familyMeta?.label ?? family.label}</h2><p className="mt-2 max-w-3xl text-sm text-foreground-subtle">{copy.question}</p>{familyMeta?.findingTypes?.length > 0 && <p className="mt-2 max-w-3xl text-xs text-foreground-subtle">Finding types: {familyMeta.findingTypes.join(" · ")}</p>}</div><div className="flex flex-wrap gap-2">{family.priority && <Badge tone={toneForPriority(family.priority)}>Highest active priority · {family.priority}</Badge>}<Badge>{formatNumber(family.active_children ?? children.length)} active investigations</Badge></div></div>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-xs text-foreground-subtle"><span>Latest observation: {observed}</span><span>Source freshness: {unknownFreshness || !detailChildren.length ? "Unknown or not recorded" : `${detailChildren.length - unknownFreshness} of ${detailChildren.length} detailed findings report freshness`}</span></div>
    </header>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{kpis.map(([label, calculate]) => <div key={label} className="rounded-xl border border-border bg-surface p-4"><div className="text-xs text-foreground-subtle">{label}</div><div className="mt-1 text-lg font-semibold text-foreground-strong">{calculate(children)}</div></div>)}</div>
    {(family.key === "warehouse_transfer_shortages" || family.key === "damage_and_writeoffs" || family.key === "inventory_health" || family.key === "inventory_opportunities") && <p className="-mt-2 text-xs text-warning">* Finding values can overlap or use different valuation coverage. Values stay per finding; they are not added as a family loss or verified saving.</p>}
    {family.key === "stock_count_controls" && <div className="rounded-xl border border-accent-border bg-accent-soft p-4"><h3 className="text-sm font-semibold text-foreground-strong">Keep count states separate</h3><p className="mt-1 text-sm text-foreground-subtle">Incomplete sessions show work remaining; completed counts show recorded variances. Unscanned value is not a variance, and duplicate-linked cases may repeat the same evidence.</p></div>}
    <section className="rounded-xl border border-border bg-surface p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-base font-semibold text-foreground-strong">Evidence by finding</h3><p className="mt-1 text-sm text-foreground-subtle">Review each finding on its own terms. Family membership does not prove a common cause.</p></div>{loading && <span className="text-xs text-foreground-subtle">Loading detail…</span>}</div><div className="mt-4"><ChildEvidence family={family} rows={children} /></div></section>
    <div className="grid gap-4 lg:grid-cols-2"><section className="rounded-xl border border-accent-border bg-accent-soft p-4"><h3 className="text-sm font-semibold text-foreground-strong">Recommended next check</h3><p className="mt-2 text-sm text-foreground">{copy.action}</p></section><details className="rounded-xl border border-border bg-surface p-4"><summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Data quality and interpretation</summary><p className="mt-2 text-sm text-foreground-subtle">{copy.limit}</p><p className="mt-2 text-sm text-foreground-subtle">Family-level action ownership and due dates are not recorded here; use each linked case to review its management history.</p></details></div>
  </section>;
}
