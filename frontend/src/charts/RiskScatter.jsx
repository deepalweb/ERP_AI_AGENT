// Scaffolded, not yet wired to a page — the stock-level-vs-sales-velocity
// risk matrix discussed for the Intelligence section (the visual seed of
// Celeste's "why are you not doing this" CHALLENGE idea). Recharts'
// ScatterChart is the right primitive when this is built. Needs a small
// backend change first: get_inventory_depth() currently only returns
// top-N-by-value per segment, not the full sold_30d/days_of_stock
// population a meaningful scatter needs — see the plan.
export default function RiskScatter() {
  return <div className="text-sm text-foreground/60">Risk matrix — not yet implemented.</div>;
}
