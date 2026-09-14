/** Combines two real systems that have never been read together for the
 * same finding: the manual action state (relevant/status — finding_actions.py,
 * set by a person via SignalCard's "Track this") and the finding's own row
 * in tracking.py's fully automatic outcome table (matched by metric_id +
 * period — /brief already returns this as `tracking`, but until now only
 * findPriorFinding() read it, and only for an earlier period, never the
 * current finding's own row). Every state here is derived from a real field
 * — nothing here is a fabricated "Improving" distinct from a real resolved
 * verdict.
 *
 * A weekly or state finding has no tracking.py row at all (that system only
 * ever covers monthly findings — see tracking.py/state_findings.py), so a
 * "completed" one of those stops at completed_unverified rather than
 * implying a verification that never ran. */

export const LIFECYCLE_META = {
  dismissed: { label: "Dismissed", tone: "neutral" },
  new: { label: "New", tone: "neutral" },
  action_required: { label: "Action required", tone: "warning" },
  in_progress: { label: "In progress", tone: "accent" },
  monitoring: { label: "Monitoring", tone: "accent" },
  completed_unverified: { label: "Completed", tone: "good" },
  resolved_improved: { label: "Resolved — Improved", tone: "good" },
  resolved_worsened: { label: "Resolved — Worsened", tone: "critical" },
  resolved_unchanged: { label: "Resolved — Unchanged", tone: "neutral" },
};

export function deriveLifecycle(finding, trackingRows) {
  const action = finding.action ?? { relevant: null, status: "not_started" };

  if (action.relevant === false) return "dismissed";
  if (action.relevant == null) return "new";
  if (action.status === "not_started") return "action_required";
  if (action.status === "in_progress") return "in_progress";

  const trackingRow = (trackingRows || []).find(
    (t) => t.metric_id === finding.metric_id && t.period === finding.confirm?.period,
  );
  if (!trackingRow) return "completed_unverified";
  if (trackingRow.status === "open") return "monitoring";
  if (trackingRow.verdict === "improved") return "resolved_improved";
  if (trackingRow.verdict === "worsened") return "resolved_worsened";
  return "resolved_unchanged";
}
