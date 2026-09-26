/** Shared customer-intelligence vocabulary -- Frontend Intelligence
 * Integration, Phase 0 extended to Customer terms. Matches
 * mcp_server/tools/state_findings.py / backend/app/services/
 * customer_predictions.py's real lifecycle categories and value tiers.
 * The Ask Celeste CustomerLookupCard and the Customers page detail view
 * both read from here, so "Lapsing" or "High value" can't mean two
 * different things depending on which one a manager is looking at. */
export const LIFECYCLE_LABEL = {
  new: "New", active: "Active", due: "Due to reorder", lapsing: "Lapsing",
  churned: "Churned", dormant: "Dormant", win_back: "Win-back", excluded: "Unscored",
};

export const VALUE_TIER_LABEL = { high: "High value", mid: "Mid value", low: "Low value" };

export const NEXT_BASKET_LABEL = { overdue: "Overdue", due: "Due", on_track: "On track" };
