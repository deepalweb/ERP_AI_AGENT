/** Lifecycle vocabulary for a persistent business case (business_cases.status
 * -- see backend/app/services/case_engine.py), distinct from a single
 * finding's action lifecycle (design/findingLifecycle.js): a case can stay
 * open for months across several findings, and its status answers "is this
 * business problem still active," not "has a manager triaged today's card."
 *
 * Every label/tone here maps 1:1 to a real backend status value -- nothing
 * here computes a priority score or re-ranks cases; that's deliberately
 * left to the backend's own last_seen_at ordering and priority/category
 * fields (see SEVERITY_TAG_META / PriorityBadge in SignalCard.jsx, reused
 * as-is for a case's `latest.priority`/`latest.category`). */
export const CASE_STATUS_META = {
  open: { label: "Open", tone: "warning" },
  verification_pending: { label: "Verification pending", tone: "accent" },
  resolved: { label: "Resolved", tone: "good" },
  dismissed: { label: "Dismissed", tone: "neutral" },
  reopened: { label: "Reopened", tone: "warning" },
};

export function caseStatusMeta(status) {
  return CASE_STATUS_META[status] ?? { label: status ?? "Unknown", tone: "neutral" };
}

// A case is "active" (still being tracked as a live problem) in exactly
// these three states -- used only to phrase copy ("still open" vs
// "closed"), never to reorder or filter the list beyond what the backend
// itself already returns for a given status filter.
export const ACTIVE_CASE_STATUSES = new Set(["open", "verification_pending", "reopened"]);
export const CLOSED_CASE_STATUSES = new Set(["resolved", "dismissed"]);
