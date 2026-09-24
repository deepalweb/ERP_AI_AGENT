/** Combines two real systems that have never been read together for the
 * same finding: the manual action state (relevant/status — finding_actions.py,
 * set by a person via SignalCard's "Track this") and a finding's own row in
 * whichever automatic outcome tracker covers its shape. Every state here is
 * derived from a real field — nothing here is a fabricated "Improving"
 * distinct from a real resolved verdict.
 *
 * Monthly findings use tracking.py's tracked_findings (matched by metric_id
 * + period). State findings (stock-outs, dead stock, dark outlets) use
 * state_tracking.py's tracked_state_findings (matched by metric_id only —
 * one open row at a time per metric). Short-term/weekly findings use
 * weekly_streaks.py's get_verdicted_streaks() (also matched by metric_id;
 * that array only ever contains rows that already picked up a verdict). If
 * none of the three has a matching row yet (genuinely too early — the
 * comparison window hasn't been reached), a "completed" action stops at
 * completed_unverified rather than implying a verification that never ran. */

export const LIFECYCLE_META = {
  dismissed: { label: "Dismissed", tone: "neutral" },
  new: { label: "New", tone: "neutral" },
  action_required: { label: "Action required", tone: "warning" },
  in_progress: { label: "In progress", tone: "accent" },
  monitoring: { label: "Monitoring", tone: "accent" },
  completed_unverified: { label: "Completed", tone: "good" },
  resolved_improved: { label: "Resolved — Improved", tone: "good" },
  // Not "Resolved — Worsened"/"Resolved — Unchanged": a worse or unchanged
  // outcome was checked, but nothing was actually resolved. Calling it
  // "resolved" reads as good news for a finding that got worse. Backing
  // tables (tracked_findings/tracked_state_findings) only ever reach this
  // branch with a non-improved verdict now that their status column
  // distinguishes 'resolved' from 'checked_unresolved' -- see tracking.py.
  checked_worsened: { label: "Checked — Still Worsening", tone: "critical" },
  checked_unchanged: { label: "Checked — No Change", tone: "neutral" },
};

function verdictToLifecycle(verdict) {
  if (verdict === "improved") return "resolved_improved";
  if (verdict === "worsened") return "checked_worsened";
  return "checked_unchanged";
}

export function deriveLifecycle(finding, trackingContext) {
  const action = finding.action ?? { relevant: null, status: "not_started" };
  const { tracking, stateTracking, streakOutcomes } = trackingContext ?? {};

  if (action.relevant === false) return "dismissed";
  if (action.relevant == null) return "new";
  if (action.status === "not_started") return "action_required";
  if (action.status === "in_progress") return "in_progress";

  if (finding.horizon === "weekly") {
    const streakRow = (streakOutcomes || []).find((r) => r.metric_id === finding.metric_id);
    return streakRow ? verdictToLifecycle(streakRow.verdict) : "completed_unverified";
  }

  const stateRow = (stateTracking || []).find((r) => r.metric_id === finding.metric_id);
  if (stateRow) {
    return stateRow.status === "open" ? "monitoring" : verdictToLifecycle(stateRow.verdict);
  }

  const trackingRow = (tracking || []).find(
    (t) => t.metric_id === finding.metric_id && t.period === finding.confirm?.period,
  );
  if (!trackingRow) return "completed_unverified";
  if (trackingRow.status === "open") return "monitoring";
  return verdictToLifecycle(trackingRow.verdict);
}
