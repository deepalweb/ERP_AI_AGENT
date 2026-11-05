import { formatCurrency } from "../format.js";

export function familyExposureLabel(child) {
  const relations = child?.related_evidence ?? [];
  if (relations.some((relation) => relation.relationship_type === "duplicate_of")) return "Duplicate evidence · do not add";
  if (relations.some((relation) => relation.relationship_type === "overlaps")) return "Overlapping evidence · may not be additive";
  const impact = child?.impact_summary;
  const value = impact?.financial_impact_rs;
  if (typeof value !== "number" || !Number.isFinite(value)) return "Not quantified";
  if (child.category === "opportunity") return `Estimated benefit · ${formatCurrency(value)}`;
  if (impact.kind === "damage_writeoff") return `Recorded write-off · estimated amount ${formatCurrency(value)}`;
  if (["stuck_stock_count_value_at_risk", "completed_stock_count_variance", "inventory_value_at_risk"].includes(impact.kind)
      || impact.valued_units_pct != null) return `Estimated exposure · ${formatCurrency(value)}`;
  return `Value basis unavailable · ${formatCurrency(value)}`;
}
