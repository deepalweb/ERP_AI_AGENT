import { Link } from "react-router-dom";
import Badge, { toneForPriority } from "./Badge";
import { caseStatusMeta } from "./caseStatus";
import { DOMAIN_LABEL } from "./domains";
import { ARCHETYPE_LABELS, resolveFindingTemplate } from "./findingTemplates";
import { formatNumber } from "../format";
import { familyExposureLabel } from "./familySummaryMath";

/** Inventory management-family summary. It aggregates counts and priority only;
 * child exposures remain separate because family membership does not prove
 * disjoint populations. Each child links to its own archetype-specific case. */
export default function FamilySummary({ family, children }) {
  if (!family) return null;
  const allChildren = family.children ?? [];
  const activeChildren = children ?? allChildren;
  const opportunityCount = allChildren.filter((child) => child.category === "opportunity").length;
  const activeCount = family.active_children ?? allChildren.length;
  const interpretation = opportunityCount === activeCount && activeCount > 0
    ? "The active findings represent opportunities."
    : opportunityCount > 0
      ? `${formatNumber(opportunityCount)} of these findings are opportunities; review each result in its own context.`
      : "Review each result independently; the family groups related findings but does not establish a shared cause.";

  return (
    <section aria-label={`${family.label} family summary`} className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Family summary</p>
          <h2 className="mt-1 font-display text-xl font-semibold text-foreground-strong">{family.label}</h2>
          <p className="mt-1 text-sm text-foreground-subtle">{interpretation}</p>
        </div>
        {family.priority && <Badge tone={toneForPriority(family.priority)}>Highest active priority · {family.priority}</Badge>}
      </div>

      <p className="mt-3 text-sm text-foreground">
        {formatNumber(activeCount)} active child finding{activeCount === 1 ? "" : "s"}
        {activeChildren.length !== activeCount ? ` · showing ${formatNumber(activeChildren.length)} matching the current filters` : ""}.
        {allChildren.some((child) => child.impact_summary?.financial_impact_rs != null)
          ? " Exposure is shown per child and is not summed because child evidence may overlap."
          : " No child exposure is quantified in the available summary."}
      </p>

      <ul className="mt-4 divide-y divide-border">
        {activeChildren.map((child) => {
          const template = resolveFindingTemplate(child);
          const status = caseStatusMeta(child.status);
          const impact = child.impact_summary;
          const scope = impact?.units_affected != null ? `${formatNumber(impact.units_affected)} units affected` : null;
          const coverage = impact?.valued_units_pct != null ? `${impact.valued_units_pct}% valued` : null;
          return (
            <li key={child.case_id} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <Link to={`/investigate/${encodeURIComponent(child.case_id)}`} className="font-medium text-accent hover:underline">
                  {child.title || child.metric_id}
                </Link>
                <p className="mt-1 text-xs text-foreground-subtle">
                  {DOMAIN_LABEL[child.domain] ?? child.domain} · {ARCHETYPE_LABELS[template.archetype]}
                  {scope ? ` · ${scope}` : ""}{coverage ? ` · ${coverage}` : ""}
                </p>
                {child.related_evidence?.length > 0 && (
                  <p className="mt-1 text-xs text-warning">
                    {child.related_evidence.map((relation, index) => (
                      <span key={`${relation.relationship_type}-${relation.case_id}`}>
                        {index > 0 ? " · " : ""}
                        {relation.relationship_type === "duplicate_of" ? "Duplicate evidence of " : "Evidence overlaps "}
                        <Link to={`/investigate/${encodeURIComponent(relation.case_id)}`} className="font-medium hover:underline">
                          {relation.title || "related finding"}
                        </Link>
                      </span>
                    ))}
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {child.priority && <Badge tone={toneForPriority(child.priority)}>{child.priority}</Badge>}
                {status && <Badge tone={status.tone}>{status.label}</Badge>}
                <span className="text-foreground-subtle">{familyExposureLabel(child)}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
