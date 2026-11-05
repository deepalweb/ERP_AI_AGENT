import { Link } from "react-router-dom";
import Badge, { toneForPriority } from "./Badge";
import { caseStatusMeta } from "./caseStatus";
import { DOMAIN_LABEL } from "./domains";
import { findingTypeFor, inventoryFamilyFor, priorityLabelFor, titleFor as standardTitleFor } from "./inventoryDisplay";
import { CATALOG_COST_ESTIMATE_KINDS } from "./overviewMath";
import { formatCurrency, formatNumber, prettifyMetricId } from "../format";

function ageDays(firstObserved) {
  if (!firstObserved) return null;
  const date = new Date(firstObserved.endsWith("Z") ? firstObserved : `${firstObserved}Z`);
  if (Number.isNaN(date.getTime())) return null;
  return Math.max(0, Math.floor((Date.now() - date.getTime()) / 86400000));
}

const COLUMN_COUNT = 8;

function ExposureTag({ text }) {
  return <span className="ml-1.5 rounded border border-border px-1 py-0.5 align-middle text-[10px] font-medium uppercase tracking-wide text-foreground-subtle">{text}</span>;
}

function IssueRow({ item, evidenceFor, typeFor, findingType, dedupeDomain }) {
  const title = standardTitleFor(item);
  const impact = item.latest?.impact_summary;
  const value = impact?.financial_impact_rs;
  const units = impact?.units_affected;
  const coverage = impact?.valued_units_pct;
  const weakValue = coverage != null && coverage < 50;
  const family = inventoryFamilyFor(item);
  const exposureSub = [units != null ? `${formatNumber(units)} units` : null, coverage != null ? `${coverage}% valued` : null].filter(Boolean).join(" · ");
  const age = ageDays(evidenceFor(item.case_id)?.first_observed);
  return (
    <tr className="border-t border-border hover:bg-surface-sunken">
      <td className="px-4 py-3">
        <Link to={`/investigate/${encodeURIComponent(item.case_id)}`} className="font-medium text-accent hover:underline">{title}</Link>
      </td>
      <td className="hidden px-4 py-3 text-foreground sm:table-cell">
        {family ? (family.domainKey === "stock_movement" ? "Stock Movement & Transfer Discrepancies"
          : family.domainKey === "stock_availability_risk" ? "Stock Availability & Stockout Risk"
          : family.domainKey === "stock_record_controls" ? "Stock Count & Inventory Record Controls"
          : family.domainKey === "damage_writeoff_losses" ? "Inventory Damage & Write-off Losses"
          : "Inventory Performance & Opportunities")
          : dedupeDomain ? <span className="text-foreground-subtle" aria-hidden="true">—</span> : (DOMAIN_LABEL[item.domain] ?? prettifyMetricId(item.domain || "unclassified"))}
      </td>
      <td className="px-4 py-3">
        {item.category === "opportunity"
          ? <span className="text-xs text-foreground-subtle">{priorityLabelFor(item, prettifyMetricId)}</span>
          : <Badge tone={toneForPriority(item.priority)}>{prettifyMetricId(item.priority || "Unranked")}</Badge>}
      </td>
      <td className="px-4 py-3 text-foreground">{caseStatusMeta(item.status).label}</td>
      <td className="hidden px-4 py-3 text-foreground sm:table-cell">{findingType(item) ?? "—"}</td>
      <td className="hidden px-4 py-3 text-foreground-subtle lg:table-cell">{typeFor(item)}</td>
      <td className="hidden whitespace-nowrap px-4 py-3 text-right tabular-nums text-foreground sm:table-cell" title="Days since this finding was first observed, not since its data last changed.">{age == null ? "n/a" : `${age} d`}</td>
      <td className="px-4 py-3 text-right tabular-nums">
        {Number.isFinite(value) ? (
          <span className={weakValue ? "text-foreground-subtle" : "text-foreground"}>
            {formatCurrency(value)}
            <ExposureTag text={item.category === "opportunity" ? "Est. benefit"
              : CATALOG_COST_ESTIMATE_KINDS.has(impact?.kind) ? "Catalog estimate"
              : impact?.kind === "completed_stock_count_variance" || impact?.kind === "damage_writeoff" ? "Recorded value"
              : "Measured"} />
          </span>
        ) : (
          <span className="text-foreground-subtle">Not quantified</span>
        )}
        {exposureSub && <div className="text-xs text-foreground-subtle">{exposureSub}</div>}
      </td>
    </tr>
  );
}

/** Issue list, grouped by management family when `groups` is given. Each group
 * header states its family's priority and that exposure is not summed across its
 * findings. Items outside every group are listed last under "Other inventory cases". */
export default function IssuesTable({ items, evidenceFor, typeFor, findingType = findingTypeFor, groups }) {
  const sections = [];
  if (groups?.length) {
    const byKey = new Map(groups.map((g) => [g.key, { ...g, items: [] }]));
    const other = [];
    for (const item of items) {
      const group = byKey.get(item.family_key);
      if (group) group.items.push(item);
      else other.push(item);
    }
    for (const group of byKey.values()) if (group.items.length) sections.push(group);
    if (other.length) sections.push({ key: "other", label: "Other cases (not in an inventory family)", items: other });
  } else {
    sections.push({ key: "all", label: null, items });
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-surface">
      <table className="w-full min-w-[480px] text-left text-sm sm:min-w-[720px]">
        <thead className="border-b border-border text-xs uppercase tracking-wider text-foreground-subtle">
          <tr>
            <th scope="col" className="px-4 py-2.5 font-medium">Issue</th>
            <th scope="col" className="hidden px-4 py-2.5 font-medium sm:table-cell">Domain</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Priority</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
            <th scope="col" className="hidden px-4 py-2.5 font-medium sm:table-cell">Finding type</th>
            <th scope="col" className="hidden px-4 py-2.5 font-medium lg:table-cell">Evidence class</th>
            <th scope="col" className="hidden px-4 py-2.5 text-right font-medium sm:table-cell" title="Days since this finding was first observed, not since its data last changed.">Age</th>
            <th scope="col" className="px-4 py-2.5 text-right font-medium">Value / benefit</th>
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => (
            <Section key={section.key} section={section} evidenceFor={evidenceFor} typeFor={typeFor} findingType={findingType} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ section, evidenceFor, typeFor, findingType }) {
  return (
    <>
      {section.label && (
        <tr className="border-t border-border bg-surface-sunken">
          <td colSpan={COLUMN_COUNT} className="px-4 py-2.5">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm font-semibold text-foreground-strong">{section.label}</span>
              {section.priority && <Badge tone={toneForPriority(section.priority)}>{prettifyMetricId(section.priority)}</Badge>}
              <span className="text-xs text-foreground-subtle">{formatNumber(section.items.length)} finding{section.items.length === 1 ? "" : "s"}</span>
              {section.note && <span className="text-xs text-foreground-subtle">{section.note}</span>}
            </div>
          </td>
        </tr>
      )}
      {section.items.map((item) => (
        <IssueRow key={item.case_id} item={item} evidenceFor={evidenceFor} typeFor={typeFor} findingType={findingType} dedupeDomain={section.key !== "other" && section.key !== "all"} />
      ))}
    </>
  );
}
