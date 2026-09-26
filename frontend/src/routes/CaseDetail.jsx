import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, MapPin } from "lucide-react";
import {
  acceptRecommendation, designateCaseObjectiveFromAction, dismissCase, fetchCaseDetail, fetchMetricDefinitions,
  reopenCase, updateFindingAction,
} from "../api";
import Badge, { toneForPriority } from "../design/Badge";
import Button from "../design/Button";
import { caseStatusMeta } from "../design/caseStatus";
import { DOMAIN_META } from "../design/domains";
import { evidenceHasChanged, latestObservationForFinding } from "../design/evidenceFreshness";
import { caseEvolutionStages } from "../design/caseEvolution";
import { OUTCOME_STATUS_META, OUTCOME_VERDICT_META, RESOLUTION_VERDICT_TEXT } from "../design/outcomeStatus";
import {
  additionalObservationsText, baselineVolatilityCaution, controlsExecutiveText, controlsResolutionText,
  decisionPromptText, evidenceRollupText, financialImpactText, headlineFor, metricSnapshotText, negativeStockText,
} from "../design/plainLanguage";
import InventoryInvestigationPanel from "../design/InventoryInvestigationPanel";
import RevenueInvestigationPanel from "../design/RevenueInvestigationPanel";
import StockCountInvestigationPanel from "../design/StockCountInvestigationPanel";
import ControlsInvestigationPanel from "../design/ControlsInvestigationPanel";
import CompletedVarianceInvestigationPanel from "../design/CompletedVarianceInvestigationPanel";
import IncidentInvestigationPanel from "../design/IncidentInvestigationPanel";
import SupplierInvestigationPanel from "../design/SupplierInvestigationPanel";
import { BusinessBrief, CaseStatusPanel, EvidenceKnowledge, PrimaryMetricStrip, TechnicalDetails } from "../design/CaseVisuals";
import { primaryMetricsFor } from "../design/caseTemplates";
import { ActionTracker, SEVERITY_TAG_META } from "../design/SignalCard";
import TrendChart from "../charts/TrendChart";
import { formatByUnit, formatConfirmPeriod } from "../format";

const HORIZON_LABEL = { monthly: "Monthly", weekly: "Weekly (trailing 7 days)", snapshot: "Point-in-time snapshot" };

const SCOPE_STATUS_TEXT = {
  segment_evidenced: "Concentrated in specific segments (below).",
  company_wide: "Checked -- spread across the business, no single dominant segment.",
  unknown: "Not enough evidence yet to say whether this is concentrated or company-wide.",
};

// The shape ActionTracker/ExistingCaseAction expect for a finding that has
// no finding_actions row yet -- mirrors finding_actions.DEFAULT_ACTION
// (backend), used only for a case's very first action (see the "Actions
// taken" fallback below).
const DEFAULT_NEW_ACTION = {
  relevant: null, status: "not_started", action_taken: null, owner: null, deadline: null,
  case_id: null, action_kind: null, completed_at: null, intended_result: null,
  target_metric_id: null, target_value: null, target_comparison: null, target_unit: null,
  target_baseline_relative: false, target_measurement_period: null,
  outcome_checked_at: null, outcome_value: null, outcome_period: null, outcome_verdict: null,
  outcome_status: null, outcome_confidence: null, evidence_verified: null, evidence_version: null,
  overdue: false, updated_at: null,
};

function toDate(value) {
  if (!value) return null;
  const iso = value.endsWith("Z") || value.includes("+") ? value : `${value}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function fmtDateTime(value) {
  const d = toDate(value);
  return d ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "n/a";
}

function parseJSON(value, fallback) {
  if (value == null) return fallback;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

const HISTORY_EVENT_META = {
  opened: { label: "Case opened", tone: "warning" },
  verification_progress: { label: "Verification progress", tone: "accent" },
  verification_reset: { label: "Verification reset -- flagged again", tone: "warning" },
  resolved: { label: "Resolved", tone: "good" },
  dismissed: { label: "Dismissed", tone: "neutral" },
  reopened: { label: "Reopened", tone: "warning" },
  decision_evidence: { label: "Decision recorded", tone: "accent" },
  recommendation_approved: { label: "Recommendation approved", tone: "good" },
};

function historyEventDescription(event) {
  const detail = parseJSON(event.detail, {});
  switch (event.event_type) {
    case "opened":
      return detail.detected_value != null ? `Detected value: ${detail.detected_value}` : null;
    case "verification_progress":
      return `Clean for ${detail.progress} consecutive period${detail.progress === 1 ? "" : "s"} (unit: ${detail.unit}).`;
    case "verification_reset":
      return detail.reason || null;
    case "resolved":
      return `Verified clean for ${detail.verification_window} consecutive periods.`;
    case "dismissed":
    case "reopened":
      return detail.reason || null;
    case "decision_evidence":
      return `Action set to "${detail.action_status}" against ${detail.finding_id}, evidence preserved as of ${fmtDateTime(detail.observed_at)}.`;
    case "recommendation_approved":
      return detail.recommendation?.action ? `"${detail.recommendation.action}"` : null;
    default:
      return null;
  }
}

function Section({ title, children, className = "" }) {
  return (
    <section id={title.toLowerCase().replace(/[^a-z0-9]+/g, "-")} className={`case-detail-section panel p-6 ${className}`}>
      <h2 className="font-display text-base font-semibold text-foreground-strong">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Celeste Investigate Phase 2's 5-category evidence taxonomy: A (the
 * measured finding itself) and B (any additional structured observation,
 * e.g. state_findings' "other top sellers under N days" list) are drawn
 * straight from `latest.confirm`/`latest.breakdowns` -- real, already-
 * measured data, never root_cause.checks. The checks themselves (grouped
 * by verdict) render via design/CaseVisuals.jsx's EvidenceSummary, shared
 * with any other case-detail-style page that needs the same grouping.
 *
 * Regression this fixes: the old EvidenceRollup only ever looked at
 * root_cause.checks, so a finding with root_cause: None (e.g. top_seller_
 * stock -- see state_findings.py's own comment on why a concentration
 * check would be meaningless there) rendered "No evidence checks recorded
 * yet", which reads as "nothing is known" when in fact the finding itself
 * IS a direct, confirmed measurement (a real stock count, not a guess) --
 * exactly the "do not treat the measured finding as nonexistent evidence"
 * instruction. */
// Case Detail V3: was a 2-part layout ("Established observations" text +
// EvidenceSummary's old 4-column confirmed/possible/unknown grid) -- now
// one EvidenceKnowledge call. measuredText/observationsText (what was
// directly measured, not a root_cause check) fold into "What we know"
// alongside any confirmed/concentration checks, instead of sitting in
// their own separate block above the checks grid.
function EvidenceSections({ latest, metricId }) {
  const measuredText = metricSnapshotText(latest, metricId)
    ?? (latest?.confirm ? `${formatByUnit(latest.confirm.current, latest.confirm.unit)} as of ${formatConfirmPeriod(latest.confirm.period)}.` : null);
  const observationsText = additionalObservationsText(latest);
  const checks = latest?.root_cause?.checks;

  return <EvidenceKnowledge measuredText={measuredText} observationsText={observationsText} checks={checks} />;
}

/** 4-stage visual stepper (Detected/Evidence/Action/Outcome) derived
 * entirely from data the page already fetched -- see design/caseEvolution.js
 * for why this isn't a reuse of findingLifecycle.js. */
const STAGE_STATE_TONE = { done: "good", current: "accent", pending: "neutral", not_applicable: "neutral" };
const STAGE_STATE_SUFFIX = { not_applicable: " (not applicable)" };

function CaseEvolutionStepper({ stages }) {
  return (
    <>
      <ol className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-0">
        {stages.map((s, i) => (
          <li key={s.key} className="flex items-center gap-2 sm:flex-1">
            <Badge tone={STAGE_STATE_TONE[s.state]} variant="dot" className={s.state === "not_applicable" ? "opacity-50" : ""}>
              {s.label}
              {STAGE_STATE_SUFFIX[s.state] || ""}
            </Badge>
            {i < stages.length - 1 && <span className="hidden h-px flex-1 bg-border sm:block" aria-hidden="true" />}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-foreground-subtle">
        Shows how far this case's evidence and management response have progressed. A case can be resolved without
        every stage being reached (e.g. a metric recovering on its own), and reaching a stage is not proof the case
        is resolved -- see Resolution status below for the actual verdict.
      </p>
    </>
  );
}

/** Inline reason-required confirmation, reused for both dismiss and reopen
 * (Step 5: "require a reason and display a confirmation before
 * consequential case changes"). No modal component exists in this app yet
 * -- an inline panel matches the existing lightweight pattern (e.g.
 * Header.jsx's ScopeChip dropdown, SignalCard's AccordionSection) rather
 * than introducing one for two call sites. */
function ConsequentialActionPanel({ label, confirmLabel, tone, onConfirm }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  if (!open) {
    return (
      <Button variant={tone === "danger" ? "danger" : "secondary"} size="sm" onClick={() => setOpen(true)}>
        {label}
      </Button>
    );
  }

  async function confirm() {
    if (!reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await onConfirm(reason.trim());
      setOpen(false);
      setReason("");
    } catch (e) {
      setError(e.message || "Could not save. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-border-strong bg-surface-sunken p-3">
      <p className="text-sm font-medium text-foreground-strong">{label} -- this is recorded in the case history.</p>
      {error && <p role="alert" className="mt-1 text-sm text-critical">{error}</p>}
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Reason (required)"
        rows={2}
        className="mt-2 w-full rounded-lg border border-border bg-surface p-2 text-sm text-foreground-strong outline-none focus:border-accent-border"
      />
      <div className="mt-2 flex gap-2">
        <Button variant={tone === "danger" ? "danger" : "primary"} size="sm" disabled={saving} onClick={confirm}>
          {confirmLabel}
        </Button>
        <Button size="sm" onClick={() => { setOpen(false); setError(""); }}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** One already-recorded action against this case -- the tracker itself,
 * plus its target/outcome card once a target has been set. Extracted so
 * it can be reused for the "no finding_actions row exists yet" case too
 * (CaseDetail's "Actions taken" section, see the fallback branch there
 * for why that's needed: a brand-new case otherwise had no action row to
 * render a tracker against at all). */
function ExistingCaseAction({
  action, staleWarning, onUpdate, metrics, caseId, onDesignateObjective, confirmStaleSave, reviewFreshEvidence,
}) {
  return (
    <div id={`action-${action.finding_id}`} className="scroll-mt-24 border-b border-border pb-4 last:border-0 last:pb-0">
      <TechnicalDetails summary="Technical details">Finding ID: {action.finding_id}</TechnicalDetails>
      {staleWarning?.findingId === action.finding_id && (
        <div className="mt-2 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm">
          <p className="font-medium text-foreground-strong">The evidence for this finding has changed since you opened this case.</p>
          <p className="mt-1 text-foreground-subtle">
            A newer scan ran while you were reviewing it. Saving now will record your decision against the current evidence,
            not what you originally saw.
          </p>
          <div className="mt-2 flex gap-2">
            <Button size="sm" variant="primary" onClick={confirmStaleSave}>
              Save with current evidence
            </Button>
            <Button size="sm" onClick={reviewFreshEvidence}>
              Review the updated evidence first
            </Button>
          </div>
        </div>
      )}
      <ActionTracker
        finding={{ id: action.finding_id, action }}
        onUpdate={onUpdate}
        showActionKind
        showTargetForm
        metrics={metrics}
        caseId={caseId}
        onDesignateObjective={onDesignateObjective}
      />
      {action.target_metric_id && (
        <div className="mt-2 rounded-lg border border-border bg-surface-sunken p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-foreground">
              Target: {action.target_metric_id} {action.target_comparison} {action.target_value}
              {action.target_unit === "percent" ? "%" : action.target_unit === "currency" ? " Rs" : ""}
              {action.target_baseline_relative ? " (relative to baseline)" : ""}
              {action.target_measurement_period === "weekly" ? " -- weekly" : " -- monthly"}
            </span>
            {action.outcome_verdict ? (
              <Badge tone={OUTCOME_VERDICT_META[action.outcome_verdict]?.tone ?? "neutral"}>
                {OUTCOME_VERDICT_META[action.outcome_verdict]?.label ?? action.outcome_verdict}
              </Badge>
            ) : (
              <Badge tone={OUTCOME_STATUS_META[action.outcome_status]?.tone ?? "neutral"}>
                {OUTCOME_STATUS_META[action.outcome_status]?.label ?? "Outcome pending"}
              </Badge>
            )}
          </div>
          {action.intended_result && <p className="mt-1 text-foreground-subtle">Intent: {action.intended_result}</p>}
          {action.outcome_verdict && (
            <p className="mt-1 text-foreground-subtle">
              Observed {action.outcome_value} in {action.outcome_period}, checked {fmtDateTime(action.outcome_checked_at)}.
              {action.outcome_confidence === "overlapping_actions" && (
                <span className="ml-1 font-medium text-warning">
                  Overlaps with another action on this case -- impact can't be confidently attributed to this one alone.
                </span>
              )}
            </p>
          )}
          {action.outcome_verdict === "met" && (
            <p className="mt-1 text-xs text-foreground-subtle">
              This confirms the target was reached after the action completed -- an observation, not proof that this action caused it.
            </p>
          )}
          {!action.outcome_verdict && action.completed_at && action.outcome_status === "pending" && (
            <p className="mt-1 text-foreground-subtle">
              Completed {fmtDateTime(action.completed_at)} -- waiting for the required post-intervention period to complete.
            </p>
          )}
          {!action.outcome_verdict && action.outcome_status === "blocked" && (
            <p className="mt-1 text-warning">
              The expected measurement is overdue -- worth a manager's follow-up.
            </p>
          )}
          {!action.outcome_verdict && action.outcome_status === "inconclusive" && (
            <p className="mt-1 text-warning">
              The required evidence never arrived within a reasonable window -- this outcome can't be established.
            </p>
          )}
          {action.evidence_verified === false && (
            <p className="mt-1 text-xs text-foreground-subtle">
              This action was last saved without a verified evidence version (a legacy save).
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Celeste Investigate Phase 2, final correction ("Recommendation vs
 * Action"): each recommendation gets its own card with TWO separate,
 * independent controls, matching three distinct concepts (Task 1):
 *
 * 1. Celeste recommendation -- `rec` itself, plain data from the finding
 *    snapshot.
 * 2. Management acceptance -- "Accept recommendation" -> onAccept ->
 *    acceptRecommendation. A pure audit decision: never creates or
 *    touches a finding_actions row (see case_engine.accept_recommendation).
 *    `accepted` comes from the case's own case_history (a
 *    recommendation_approved event whose recommendation.action matches
 *    this card -- see acceptedRecommendationTexts below), never
 *    substring-matching action_taken.
 * 3. Recorded action -- "Record action" / "View recorded action", a
 *    plain same-page anchor into the real "Actions taken" tracker below
 *    (id={`action-${findingId}`}), the existing tracker workflow,
 *    unchanged. Accepting a recommendation never implies a task is
 *    underway; recording one is a wholly separate, explicit step a
 *    manager takes in that tracker. `hasRecordedAction` switches the
 *    link's label once a real row exists, so it reads as "go see it"
 *    rather than "start one" (Task 4: "a clear link to it"). */
function RecommendationCard({ rec, accepted, hasRecordedAction, findingId, onAccept, affectedRecordCount }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function accept() {
    setSaving(true);
    setError("");
    try {
      await onAccept(rec);
    } catch (e) {
      setError(e.message || "Could not record this acceptance. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-surface-sunken p-3.5">
      <div className="flex items-start justify-between gap-3">
        <span className="text-sm leading-snug text-foreground-strong">{rec.action}</span>
        <Badge tone={toneForPriority(rec.priority)} className="flex-none">{rec.priority}</Badge>
      </div>
      <div className="mt-1.5 text-xs text-foreground-subtle">Owner: {rec.owner ?? "Unassigned"}</div>
      {rec.expected_benefit && (
        <div className="mt-1.5 text-xs text-foreground-subtle">
          <strong className="font-medium text-foreground">Expected result -- </strong>
          {rec.expected_benefit}
        </div>
      )}
      {/* Case Detail V3: points at the affected-record table already shown
          in the investigation panel above, instead of listing the records
          again in this card's own text (the exact "product names embedded
          in the recommendation prose" complaint this redesign fixes). */}
      {affectedRecordCount != null && (
        <a href="#control-investigation" className="mt-1.5 block text-xs font-medium text-accent hover:underline">
          {affectedRecordCount} affected record{affectedRecordCount === 1 ? "" : "s"} &rarr; view above
        </a>
      )}
      {error && (
        <p role="alert" className="mt-2 text-xs text-critical">{error}</p>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {accepted ? (
          <Badge tone="good">Accepted</Badge>
        ) : (
          <Button size="sm" variant="secondary" disabled={saving} onClick={accept}>
            {saving ? "Recording..." : "Accept recommendation"}
          </Button>
        )}
        <a href={`#action-${findingId}`} className="text-xs font-medium text-accent hover:underline">
          {hasRecordedAction ? "View recorded action" : "Record action"} &rarr;
        </a>
      </div>
    </div>
  );
}

export default function CaseDetail() {
  const { caseId } = useParams();
  const queryClient = useQueryClient();
  const detailQuery = useQuery({ queryKey: ["caseDetail", caseId], queryFn: () => fetchCaseDetail(caseId) });
  const [searchParams] = useSearchParams();
  const targetAction = searchParams.get("action");
  useEffect(() => {
    if (targetAction && detailQuery.data) document.getElementById(`action-${targetAction}`)?.scrollIntoView({ block: "center" });
  }, [targetAction, detailQuery.data]);
  // The server's own metric registry (id/label/unit/supports_weekly_
  // measurement) -- the target form (SignalCard.ActionTargetForm) is built
  // entirely from this, never a hardcoded or guessed metric list. Rarely
  // changes, so a plain query with no case-specific key is fine.
  const metricsQuery = useQuery({ queryKey: ["metricDefinitions"], queryFn: fetchMetricDefinitions });

  function refresh() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ["caseDetail", caseId] }),
      queryClient.invalidateQueries({ queryKey: ["management"] }),
      queryClient.invalidateQueries({ queryKey: ["cases"] }),
      queryClient.invalidateQueries({ queryKey: ["brief"] }),
    ]);
  }

  // kind distinguishes which retry path confirmStaleSave should take --
  // { kind: "action", findingId, patch, version } for the real "Record
  // action" tracker, { kind: "acceptance", findingId, recommendationAction,
  // version } for "Accept recommendation". Two different server calls
  // (updateFindingAction vs acceptRecommendation -- see Task 3 of the
  // Celeste Investigate Phase 2 final correction), so a stale-evidence
  // conflict from either has to be retried through the SAME call it came
  // from, not the generic action-save one.
  const [staleWarning, setStaleWarning] = useState(null);
  const [retryNotice, setRetryNotice] = useState("");

  async function saveWithVersion(findingId, patch, version) {
    try {
      await updateFindingAction(findingId, { ...patch, evidence_version: version ?? null });
      await refresh();
    } catch (error) {
      // Server-side reassertion of the same check just below -- catches
      // the rarer race where evidence changed AFTER the client's own
      // pre-save refetch but before this write actually landed. The
      // conflict response already carries the fresh evidence, so no
      // second round trip is needed to show it.
      if (error.body?.error === "stale_evidence") {
        setStaleWarning({ kind: "action", findingId, patch, version: error.body.current_version });
        return;
      }
      // A scan is actively holding the same lock this save needs (see
      // case_engine.save_action_with_verification) -- nothing is stale,
      // it just needs a moment. Surfaced distinctly from a real conflict
      // so the manager knows to simply try again shortly.
      if (error.body?.error === "scan_in_progress") {
        setRetryNotice("A scan is currently running. Please try saving again in a moment.");
        return;
      }
      // Visual Investigation V2, Phase 1 (confirmed regression): any OTHER
      // error -- a 502 with no parseable body, a dropped connection, a
      // timeout -- means the write's actual result is genuinely unknown:
      // it may have committed server-side even though this response never
      // arrived intact. Refreshing from the server (not just showing the
      // error) means the NEXT render reflects whatever really happened, so
      // a retry naturally sees "Accepted"/the saved fields instead of
      // resubmitting a write that already landed. Never silently retries
      // on its own -- the manager still sees the error and decides.
      await refresh();
      throw error;
    }
  }

  async function handleUpdateAction(findingId, patch) {
    // Re-fetches fresh (not from the React Query cache) so this compares
    // against what's actually in the database right now, not whatever was
    // last rendered -- a scan can run in the gap between opening this
    // page and saving. If nothing changed, save proceeds exactly as
    // before; if it did, pause and let the manager choose rather than
    // silently attributing their decision to evidence they never saw
    // (case_engine.record_decision_snapshot always captures CURRENT
    // evidence at save time -- correct, but only if the manager knows).
    const fresh = await fetchCaseDetail(caseId);
    const viewedObservations = detailQuery.data?.observations;
    const freshObservation = fresh.status === "ok" ? latestObservationForFinding(fresh.observations, findingId) : null;
    if (fresh.status === "ok" && evidenceHasChanged(viewedObservations, fresh.observations, findingId)) {
      setStaleWarning({ kind: "action", findingId, patch, version: freshObservation?.version });
      return;
    }
    await saveWithVersion(findingId, patch, freshObservation?.version);
  }

  // Celeste Investigate Phase 2, final correction ("Recommendation vs
  // Action"): accepting a recommendation is an auditable planning
  // decision, never an operational action -- this hits its own endpoint
  // (acceptRecommendation -> POST /findings/{id}/recommendation-
  // acceptance -> case_engine.accept_recommendation) which never creates
  // or touches a finding_actions row (the earlier "Approve as action"
  // revision did, via updateFindingAction -- a "phantom action" this
  // correction removes). Same evidence-version check/lock/transaction as
  // the action-save path, just without an action write inside it.
  async function acceptWithVersion(findingId, recommendationAction, version) {
    try {
      await acceptRecommendation(findingId, { recommendation_action: recommendationAction, evidence_version: version ?? null });
      await refresh();
    } catch (error) {
      if (error.body?.error === "stale_evidence") {
        setStaleWarning({ kind: "acceptance", findingId, recommendationAction, version: error.body.current_version });
        return;
      }
      if (error.body?.error === "scan_in_progress") {
        setRetryNotice("A scan is currently running. Please try saving again in a moment.");
        return;
      }
      // See saveWithVersion's own comment on this same pattern: an
      // unstructured failure (502, dropped connection, timeout) means the
      // acceptance's actual result is unknown -- refresh so a retry sees
      // "Accepted" if it already landed, instead of logging a duplicate
      // recommendation_approved event (record_recommendation_acceptance
      // has no idempotency guard of its own; this is the defense).
      await refresh();
      throw error;
    }
  }

  async function handleAcceptRecommendation(findingId, recommendationAction) {
    const fresh = await fetchCaseDetail(caseId);
    const viewedObservations = detailQuery.data?.observations;
    const freshObservation = fresh.status === "ok" ? latestObservationForFinding(fresh.observations, findingId) : null;
    if (fresh.status === "ok" && evidenceHasChanged(viewedObservations, fresh.observations, findingId)) {
      setStaleWarning({ kind: "acceptance", findingId, recommendationAction, version: freshObservation?.version });
      return;
    }
    await acceptWithVersion(findingId, recommendationAction, freshObservation?.version);
  }

  async function confirmStaleSave() {
    if (!staleWarning) return;
    const w = staleWarning;
    setStaleWarning(null);
    if (w.kind === "acceptance") {
      await acceptWithVersion(w.findingId, w.recommendationAction, w.version);
    } else {
      await saveWithVersion(w.findingId, w.patch, w.version);
    }
  }

  async function reviewFreshEvidence() {
    setStaleWarning(null);
    await refresh();
  }

  async function handleDesignateObjective(findingId) {
    await designateCaseObjectiveFromAction(caseId, findingId);
    await refresh();
  }

  if (detailQuery.isLoading) {
    return (
      <div className="px-6 py-6 sm:px-8">
        <div className="h-8 w-48 animate-pulse rounded bg-surface" />
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-40 animate-pulse rounded-xl border border-border bg-surface" />
          ))}
        </div>
      </div>
    );
  }

  if (detailQuery.isError || detailQuery.data?.status !== "ok") {
    return (
      <div className="px-6 py-6 sm:px-8">
        <Link to="/investigate" className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
          <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} /> Back to Investigate
        </Link>
        <div className="mt-4 flex items-center gap-2 rounded-xl border border-critical/25 bg-critical/10 p-4 text-sm text-critical">
          <AlertCircle className="h-4 w-4 flex-none" strokeWidth={2} />
          {detailQuery.data?.detail || "Couldn't load this case."}
        </div>
      </div>
    );
  }

  const { case: c, latest, observations, observations_note: observationsNote, performance_history: performanceHistory,
    relationships, history, actions, resolution_policy: resolutionPolicy } = detailQuery.data;

  const statusMeta = caseStatusMeta(c.status);
  const severityMeta = latest ? SEVERITY_TAG_META[latest.priority] ?? null : null;
  const title = latest?.title ?? c.metric_id.replace(/_/g, " ");
  const headline = latest ? headlineFor(latest) : null;
  const scopeSegments = parseJSON(c.scope_segments, []);
  const isActive = c.status === "open" || c.status === "verification_pending" || c.status === "reopened";
  const isClosed = c.status === "resolved" || c.status === "dismissed";
  const sortedHistory = [...(history || [])].sort((a, b) => (a.at < b.at ? 1 : -1));
  const trendPoints = (performanceHistory || []).map((p) => ({ month: p.scan_run_id, value: p.current, is_partial: false }));

  // Case Detail V3's Business Brief: split into two pieces a manager reads
  // separately -- the brief paragraph (what happened, why uncertain,
  // impact) and a standalone "Next decision" line, instead of one run-on
  // sentence ending in a generic decision prompt. SCOPE_STATUS_TEXT stays
  // out of the brief (duplicates evidenceRollupText -- shown once, in the
  // collapsed technical details). controlsExecText (below) overrides the
  // brief for a controls case; the generic chain is the fallback for
  // everything else, including an unrecognized controls metric_id.
  const genericBrief = latest
    ? [headline, negativeStockText(latest), evidenceRollupText(latest.root_cause), baselineVolatilityCaution(latest.confirm), financialImpactText(latest.impact)]
        .filter(Boolean)
        .join(" ")
    : null;
  // The concrete next step (a real recommendation) is more useful than the
  // generic "review the evidence" prompt whenever one exists; the prompt
  // is still the fallback for a case with no recommendations yet.
  const nextDecision = latest?.recommendations?.[0]?.action ?? decisionPromptText(c.status);
  const evolutionStages = caseEvolutionStages(c, actions, observations);
  // Whether a REAL finding_actions row exists for latest.finding_id
  // (TODAY's evidence snapshot) -- distinct from actions[0], which is the
  // OLDEST row across the case's whole lifetime (get_actions_for_case
  // returns every finding_id this case has ever had, updated_at ASC), not
  // reliably "the current one" once a case has more than one. Drives the
  // recommendation cards' "Record action" vs "View recorded action" link
  // label (Task 4: "provide a clear link to it" once one exists).
  const hasRecordedAction = (actions ?? []).some((a) => a.finding_id === latest?.finding_id);
  // Which recommendations have already been ACCEPTED (a planning decision,
  // never an operational action -- see RecommendationCard's own docstring),
  // read from the case's own real audit log (case_history's
  // recommendation_approved events) rather than inferred from action_taken
  // text.
  const acceptedRecommendationTexts = new Set(
    (history ?? [])
      .filter((e) => e.event_type === "recommendation_approved")
      .map((e) => parseJSON(e.detail, {}).recommendation?.action)
      .filter(Boolean),
  );
  // Frontend Intelligence Integration, Phase 1: layered routing -- a
  // metric-specific panel first (a finding shape richer than its domain
  // implies), then a domain-level panel, then the generic trend-or-nothing
  // fallback. Metric-specific still wins over domain so a future domain
  // panel never accidentally shadows one of these three.
  const isInventoryStockOut = c.metric_id === "top_seller_stock";
  const isRevenueCase = c.metric_id === "revenue";
  const isStuckStockCount = c.metric_id === "stuck_stock_count";
  const isCompletedVarianceCase = c.metric_id === "completed_stock_count_variance";
  // incident_engine synthesizes a scope-specific metric_id per store/
  // product/hour (incident_s{store_id}_p{product_id}_h{hour}) -- never a
  // fixed string, so it can't be matched like the ones above.
  const isIncidentCase = typeof c.metric_id === "string" && c.metric_id.startsWith("incident_");
  const isControlsCase = c.domain === "controls";
  const isSupplierCase = c.domain === "supplier";
  // Business Controls cases (a data-control gap, not a trend anomaly) read
  // as generic through the default sentence chain -- see plainLanguage.js's
  // controlsExecutiveText/controlsResolutionText docstrings for why. Both
  // return null for anything they don't specifically recognize, so the
  // existing generic text is always the fallback, never silently blank.
  const controlsExecText = isControlsCase ? controlsExecutiveText(latest) : null;
  const controlsResText = isControlsCase ? controlsResolutionText(latest, c.status) : null;

  // Case Detail V3's Primary Metric Strip: one finding-type-specific KPI
  // row (design/caseTemplates.js) instead of a fixed 4-card shape forced
  // onto every metric -- see that file for what each type gets. Empty for
  // a controls case (its own panel already has this row -- see below).
  const primaryMetrics = primaryMetricsFor({ c, latest, statusLabel: statusMeta.label });

  return (
    <div className="workspace-page case-detail-page">
      <Link to="/investigate" className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
        <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} /> Back to Investigate
      </Link>

      {retryNotice && (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm text-foreground">
          {retryNotice}
          <Button size="sm" onClick={() => setRetryNotice("")}>Dismiss</Button>
        </div>
      )}

      {/* 1. Case header. */}
      <div className="mt-6">
        <CaseStatusPanel
          title={title}
          domainLabel={DOMAIN_META[c.domain]?.label ?? c.domain}
          statusBadge={<Badge tone={statusMeta.tone}>{statusMeta.label}</Badge>}
          severityBadge={severityMeta && <Badge tone={toneForPriority(latest.priority)}>{severityMeta.label}</Badge>}
          lastObservedLabel={`Last observed ${fmtDateTime(c.last_seen_at)}`}
          actionSlot={
            <>
              {isActive && (
                <ConsequentialActionPanel
                  label="Dismiss case"
                  confirmLabel="Confirm dismissal"
                  tone="danger"
                  onConfirm={async (reason) => { await dismissCase(caseId, reason); await refresh(); }}
                />
              )}
              {isClosed && (
                <ConsequentialActionPanel
                  label="Reopen case"
                  confirmLabel="Confirm reopen"
                  tone="primary"
                  onConfirm={async (reason) => { await reopenCase(caseId, reason); await refresh(); }}
                />
              )}
            </>
          }
        />
      </div>

      {/* 2. Business brief -- what happened, why uncertain, impact, and a
          standalone next-decision line. controlsExecText overrides the
          generic sentence chain for a controls case (see plainLanguage.js);
          nextDecision is the same for every case type (a real
          recommendation when one exists, else the generic decision
          prompt). */}
      {(controlsExecText || genericBrief) && (
        <div className="mt-4">
          <Section title="Business brief">
            <BusinessBrief brief={controlsExecText || genericBrief} nextDecision={nextDecision} />
          </Section>
        </div>
      )}

      {/* 3. Primary metrics -- one finding-type-specific KPI row (see
          design/caseTemplates.js), empty for a controls case since its own
          panel below already has one. */}
      {primaryMetrics.length > 0 && (
        <div className="mt-4">
          <PrimaryMetricStrip cards={primaryMetrics} />
        </div>
      )}

      {/* Investigation + decision, side by side: what happened/what we
          know on the left (the reading half), what management decides on
          the right (the deciding half) -- Case Detail V4's management-
          flow layout, replacing the earlier equal-weighted vertical stack
          of same-width cards. */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="flex flex-col gap-4 lg:col-span-8">
          {/* Investigation content -- rendered directly, no "Visual
              investigation" heading over an empty section. Only a
              dedicated panel or a real multi-point trend renders anything
              at all; a metric with neither renders nothing here rather
              than an empty labeled box. */}
          {(isStuckStockCount || isInventoryStockOut || isRevenueCase || isCompletedVarianceCase || isIncidentCase
            || isControlsCase || isSupplierCase || trendPoints.length > 1) && (
            <Section title={isControlsCase ? "Control investigation" : "Investigation"}>
              {isStuckStockCount ? (
                <StockCountInvestigationPanel latest={latest} trendPoints={trendPoints} onRetry={refresh} />
              ) : isInventoryStockOut ? (
                <InventoryInvestigationPanel latest={latest} asOfDate={latest?.confirm?.period} />
              ) : isRevenueCase ? (
                <RevenueInvestigationPanel latest={latest} casePeriod={latest?.confirm?.period} />
              ) : isCompletedVarianceCase ? (
                <CompletedVarianceInvestigationPanel latest={latest} />
              ) : isIncidentCase ? (
                <IncidentInvestigationPanel latest={latest} />
              ) : isControlsCase ? (
                <ControlsInvestigationPanel latest={latest} statusLabel={statusMeta.label} />
              ) : isSupplierCase ? (
                <SupplierInvestigationPanel latest={latest} />
              ) : (
                <div>
                  <p className="mb-2 text-xs text-foreground-subtle">Recorded observations over time (real scan history, never interpolated).</p>
                  <TrendChart data={trendPoints} formatValue={(v) => formatByUnit(v, latest?.confirm?.unit)} stroke="var(--accent)" gridStroke="var(--border)" axisStroke="var(--text-subtle)" />
                </div>
              )}
            </Section>
          )}

          {/* Evidence -- "What we know / may be related / don't know".
              Skipped for a controls case: ControlsInvestigationPanel
              already has its own Established/Not-established block, built
              with domain knowledge (e.g. "what the correct replacement
              cost should be") a generic checks-only bucketing can't know. */}
          {!isControlsCase && (
            <Section title="Evidence">
              <EvidenceSections latest={latest} metricId={c.metric_id} />
            </Section>
          )}
        </div>

        <div className="flex flex-col gap-4 lg:col-span-4">
          <Section title="Proposed by Celeste">
            {latest?.recommendations?.length > 0 && latest?.finding_id ? (
              <div className="space-y-3">
                {latest.recommendations.map((rec, i) => (
                  <RecommendationCard
                    key={i}
                    rec={rec}
                    accepted={acceptedRecommendationTexts.has(rec.action)}
                    hasRecordedAction={hasRecordedAction}
                    findingId={latest.finding_id}
                    onAccept={(r) => handleAcceptRecommendation(latest.finding_id, r.action)}
                    affectedRecordCount={isControlsCase ? latest.products?.length : undefined}
                  />
                ))}
              </div>
            ) : (
              <p className="text-sm text-foreground-subtle">No recommendations generated for this case yet.</p>
            )}
          </Section>

          {/* Was "Actions taken (N)" -- a manager's own next step, not a
              log entry; relabeled to read as a follow-up prompt. */}
          <Section title="Management follow-up">
            {actions?.length > 0 ? (
              <div className="space-y-4">
                {actions.map((action) => (
                  <ExistingCaseAction
                    key={action.finding_id}
                    action={action}
                    staleWarning={staleWarning}
                    onUpdate={handleUpdateAction}
                    metrics={metricsQuery.data?.metrics ?? []}
                    caseId={caseId}
                    onDesignateObjective={handleDesignateObjective}
                    confirmStaleSave={confirmStaleSave}
                    reviewFreshEvidence={reviewFreshEvidence}
                  />
                ))}
              </div>
            ) : latest?.finding_id ? (
              // A brand-new case has no finding_actions row for anything yet
              // -- without this, there was no way to record a FIRST action
              // on a case at all (found via the Phase 2e post-acceptance
              // browser test). latest.finding_id is the most recent
              // evidence's own finding id (see case_engine._summarize_
              // snapshot), the same id an action save would resolve to this
              // case through (case_engine.get_case_id_for_finding). Routed
              // through the same ExistingCaseAction as a real row (not a
              // bare ActionTracker) so the stale-evidence banner -- which
              // only that wrapper renders -- still works on a first save;
              // an earlier version of this fallback used ActionTracker
              // directly and silently dropped that warning (also found via
              // the browser test).
              <ExistingCaseAction
                action={{ ...DEFAULT_NEW_ACTION, finding_id: latest.finding_id }}
                staleWarning={staleWarning}
                onUpdate={handleUpdateAction}
                metrics={metricsQuery.data?.metrics ?? []}
                caseId={caseId}
                onDesignateObjective={handleDesignateObjective}
                confirmStaleSave={confirmStaleSave}
                reviewFreshEvidence={reviewFreshEvidence}
              />
            ) : (
              <p className="text-sm text-foreground-subtle">No action recorded yet.</p>
            )}
          </Section>

          {/* Resolution -- stays visible (this is part of "was it
              resolved", not audit trail); the business objective and any
              recurrence relationship are decision-relevant context, so
              they stay here too rather than moving into the collapsed
              technical section below. */}
          <Section title="Resolution">
            <p className="text-sm text-foreground">
              {controlsResText ? controlsResText : (
                <>
                  {c.status === "resolved" && RESOLUTION_VERDICT_TEXT[c.resolution_verdict] ? RESOLUTION_VERDICT_TEXT[c.resolution_verdict] :
                    c.status === "resolved" && `Verified resolved -- clean for ${resolutionPolicy?.verification_window ?? 2} consecutive periods with no fresh anomaly.`}
                  {c.status === "verification_pending" &&
                    `Recovering, not yet confirmed -- clean for ${c.verification_progress} of ${resolutionPolicy?.verification_window ?? 2} required periods. `
                    + "If a relevant, approved action's outcome is still awaiting measurement, closure is held until that's known, even once the metric itself has cleared."}
                  {c.status === "open" && "Still active. Celeste has not yet seen this clear its own anomaly threshold."}
                  {c.status === "reopened" && "Reopened -- a matching problem reappeared after this case was closed."}
                  {c.status === "dismissed" && "Dismissed -- not verified as resolved, marked as not a real problem."}
                </>
              )}
            </p>

            {c.objective_metric_id && (
              <div className="mt-3 border-t border-border pt-3 text-sm">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Business objective</span>
                <p className="mt-1 text-foreground">
                  {c.objective_metric_id} {c.objective_comparison} {c.objective_target_value}
                  {c.objective_unit === "percent" ? "%" : c.objective_unit === "currency" ? " Rs" : ""}
                  {c.objective_baseline_relative ? " (relative to baseline)" : ""}
                  {c.objective_source_finding_id && (
                    <span className="text-foreground-subtle"> -- designated from action {c.objective_source_finding_id}</span>
                  )}
                </p>
              </div>
            )}

            {relationships?.length > 0 && (
              <ul className="mt-3 space-y-1.5 border-t border-border pt-3 text-sm">
                {relationships.map((r, i) => {
                  const isSource = r.case_id === c.case_id;
                  const otherId = isSource ? r.related_case_id : r.case_id;
                  return (
                    <li key={i} className="text-foreground-subtle">
                      {r.relationship_type === "recurrence_of" && isSource && (
                        <>
                          This is a recurrence of{" "}
                          <Link to={`/investigate/${encodeURIComponent(otherId)}`} className="font-medium text-accent hover:underline">
                            an earlier, resolved case
                          </Link>
                          .
                        </>
                      )}
                      {r.relationship_type === "recurrence_of" && !isSource && (
                        <>
                          <Link to={`/investigate/${encodeURIComponent(otherId)}`} className="font-medium text-accent hover:underline">
                            A later case
                          </Link>{" "}
                          recurred from this one.
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}

            {c.dismissed_reason && (
              <p className="mt-3 border-t border-border pt-3 text-sm text-foreground-subtle">
                <span className="font-medium text-foreground">Dismissed:</span> {c.dismissed_reason}
              </p>
            )}
          </Section>

          {/* Priority details -- the internal ranking score, demoted to a
              small collapsed disclosure rather than an equally-weighted
              card next to the actual business numbers (which live in the
              investigation panel on the left). */}
          <details className="rounded-xl border border-border bg-surface p-4">
            <summary className="cursor-pointer text-sm font-medium text-foreground-strong">
              Why this is {latest?.priority ? `${latest.priority.replace(/_/g, " ")} priority` : "unscored"}
            </summary>
            <p className="mt-2 text-sm text-foreground-subtle">
              Internal priority score: {c.business_impact_score != null ? Number(c.business_impact_score).toFixed(1) : "not scored"} --
              severity &times; persistence &times; whether impact was quantified, for Celeste's own case ranking only, never a
              currency figure or a probability.
            </p>
          </details>
        </div>
      </div>

      {/* 10. History and technical details, collapsed by default -- case
          evolution, where/when/scope, full audit log, and observation
          policy all move here (were previously always-visible), matching
          the "these are important for auditability, but don't belong in
          the main reading path" instruction. */}
      <div className="mt-4">
        <Section title="History and technical details">
          <TechnicalDetails summary="Case evolution">
            <div className="mt-2">
              <CaseEvolutionStepper stages={evolutionStages} />
            </div>
          </TechnicalDetails>

          <TechnicalDetails summary="Where and when">
            <dl className="mt-2 space-y-2">
              <div className="flex justify-between gap-3">
                <dt className="text-foreground-subtle">Reporting scope</dt>
                <dd className="text-foreground-strong">{HORIZON_LABEL[c.horizon] ?? c.horizon}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-foreground-subtle">Opened</dt>
                <dd className="text-foreground-strong">{fmtDateTime(c.opened_at)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-foreground-subtle">Last observed</dt>
                <dd className="text-foreground-strong">{fmtDateTime(c.last_seen_at)}</dd>
              </div>
              {c.closed_at && (
                <div className="flex justify-between gap-3">
                  <dt className="text-foreground-subtle">Closed</dt>
                  <dd className="text-foreground-strong">{fmtDateTime(c.closed_at)}</dd>
                </div>
              )}
            </dl>
            <div className="mt-2 flex items-start gap-2 border-t border-border pt-2">
              <MapPin className="mt-0.5 h-3.5 w-3.5 flex-none text-foreground-subtle" strokeWidth={2} />
              <div>
                <p className="text-foreground">{SCOPE_STATUS_TEXT[c.scope_status] ?? "Unknown scope."}</p>
                {scopeSegments.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {scopeSegments.map((s, i) => (
                      <span key={i} className="rounded-md border border-border bg-surface-sunken px-2 py-0.5 text-xs text-foreground-strong">
                        {s.segment_name} <span className="text-foreground-subtle">({s.dimension})</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </TechnicalDetails>

          <TechnicalDetails summary={`Case history (${sortedHistory.length} event${sortedHistory.length === 1 ? "" : "s"})`}>
            <ul className="space-y-3">
              {sortedHistory.map((event) => {
                const meta = HISTORY_EVENT_META[event.event_type] ?? { label: event.event_type, tone: "neutral" };
                const description = historyEventDescription(event);
                return (
                  <li key={event.id} className="flex items-start gap-3 text-sm">
                    <Badge tone={meta.tone} variant="dot" className="mt-0.5 flex-none">
                      {meta.label}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      {description && <p className="text-foreground">{description}</p>}
                      <p className="text-xs text-foreground-subtle">{fmtDateTime(event.at)}</p>
                    </div>
                  </li>
                );
              })}
              {sortedHistory.length === 0 && <p className="text-sm text-foreground-subtle">No history yet.</p>}
            </ul>

            {observations?.length > 0 && (
              <div className="mt-4 border-t border-border pt-3">
                <p className="text-xs text-foreground-subtle">
                  {observations.length} observation{observations.length === 1 ? "" : "s"} recorded, most recent{" "}
                  {fmtDateTime(observations[observations.length - 1].observed_at)}.
                </p>
                <TechnicalDetails summary="What is an observation?">{observationsNote}</TechnicalDetails>
              </div>
            )}
          </TechnicalDetails>
        </Section>
      </div>
    </div>
  );
}
