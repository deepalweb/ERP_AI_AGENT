import TrendChart from "../charts/TrendChart";
import RankedBarChart from "../charts/RankedBarChart";
import Badge from "./Badge";
import { ROOT_CAUSE_VERDICT_LABEL, ROOT_CAUSE_VERDICT_TONE } from "./SignalCard";
import { formatByUnit } from "../format";

/** Visual Investigation V2, Phase 3: reusable pieces justified by real data
 * on the stuck_stock_count case (and reused as-is by any other case shape
 * whose data fits the same contract) -- never a per-metric one-off unless
 * the metric's own data genuinely doesn't fit these shapes, in which case
 * the caller falls back to the generic Case Detail sections instead of
 * forcing a mismatched visualization. */

/** Compact KPI tile for "Situation at a glance". Deliberately renders "—"
 * (never 0) when `value` is null/undefined -- an unavailable measurement is
 * not the same claim as a real zero, and this must never blur the two. */
export function MetricSummaryCard({ label, value, caption, valueClassName }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface-sunken p-3.5">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">{label}</span>
      <strong className={`text-xl font-bold tabular-nums ${valueClassName ?? "text-foreground-strong"}`}>
        {value == null || value === "" ? "—" : value}
      </strong>
      {caption && <span className="text-xs text-foreground-subtle">{caption}</span>}
    </div>
  );
}

/** A. Observation trend -- plots ONLY real recorded observations (never
 * interpolated or invented ones). `points` is the same {month, value,
 * is_partial} shape TrendChart already consumes elsewhere in this app;
 * pass real `caseEngine.get_case_observations`-derived points (e.g.
 * CaseDetail's own `trendPoints`, built from `performance_history`), never
 * a synthesized series. Explicitly reports when there's only one point (a
 * line chart of one dot proves nothing) rather than rendering a
 * misleadingly flat or empty chart. */
export function ObservationTrend({ points, formatValue = String, formatX = String, unitLabel }) {
  const real = (points ?? []).filter((p) => p.value != null);
  if (real.length === 0) {
    return <p className="text-sm text-foreground-subtle">No observations recorded yet.</p>;
  }
  if (real.length === 1) {
    return (
      <p className="text-sm text-foreground-subtle">
        Only one observation recorded so far ({formatX(real[0].month)}: {formatValue(real[0].value)}
        {unitLabel ? ` ${unitLabel}` : ""}) -- not enough to show a trend yet.
      </p>
    );
  }
  return (
    <TrendChart
      data={real}
      formatValue={formatValue}
      formatX={formatX}
      stroke="var(--accent)"
      gridStroke="var(--border)"
      axisStroke="var(--text-subtle)"
    />
  );
}

/** B. Contribution / concentration comparison -- a plain magnitude ranking
 * (RankedBarChart), with the caller REQUIRED to state what the bars measure
 * (`unitLabel`, e.g. "Rs estimated value of unscanned lines" or "stuck sessions") so a
 * reader never has to guess whether they're looking at a count or a value.
 * `periodLabel` is optional context (e.g. a month range) for when the
 * comparison spans two specific periods, same "name the exact span"
 * discipline as the revenue panel's move_span labeling. */
export function ContributionComparison({ data, unitLabel, periodLabel, valueKey = "current", formatValue = String }) {
  if (!data || data.length === 0) {
    return <p className="text-sm text-foreground-subtle">No contribution data available.</p>;
  }
  return (
    <div>
      <p className="mb-2 text-xs text-foreground-subtle">
        Shows {unitLabel}{periodLabel ? ` (${periodLabel})` : ""}.
      </p>
      <RankedBarChart data={data} valueKey={valueKey} formatValue={formatValue} />
    </div>
  );
}

/** C. Affected-record table -- a generic, column-configurable table for
 * real structured records (stuck sessions, out-of-stock products, etc.).
 * `columns` is [{ key, label, format? }]; a row's `key` value is read
 * directly, never scraped from another field's prose text. `rows` must
 * already be real structured data (from the finding's own payload or a
 * read-only API addition) -- this component never manufactures a record. */
export function AffectedRecordTable({ columns, rows, rowKey = "id", onRowClick, selectedRowKey }) {
  if (!rows || rows.length === 0) {
    return <p className="text-sm text-foreground-subtle">No affected records available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground-subtle">
            {columns.map((col) => (
              <th key={col.key} className="pb-2 pr-3 font-medium">{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row[rowKey]}
              className={`border-t border-border${onRowClick ? " cursor-pointer hover:bg-surface-sunken" : ""}${
                selectedRowKey != null && row[rowKey] === selectedRowKey ? " bg-surface-sunken" : ""
              }`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col) => (
                <td key={col.key} className="py-1.5 pr-3 text-foreground">
                  {col.format ? col.format(row[col.key], row) : (row[col.key] ?? "n/a")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 5. Evidence and uncertainty -- the root_cause.checks portion, three
 * tiers (NOT four: "established observations" is the measured finding
 * itself, a separate concept this component doesn't cover -- see
 * CaseDetail.jsx's EvidenceSections, which renders that part and this
 * component together). A confirmed verdict is EXCLUSIVELY produced by the
 * concentration checks in this app's evidence engine (evidence_engine.py's
 * own module docstring states this twice -- see plainLanguage.
 * evidenceRollupText's docstring for the full reasoning), so "confirmed"
 * is labeled "Supported contribution or concentration" here, never
 * "established cause". */
export function EvidenceSummary({ checks }) {
  const hasChecks = Boolean(checks && checks.length > 0);
  const confirmed = hasChecks ? checks.filter((c) => c.verdict === "confirmed") : [];
  const possible = hasChecks ? checks.filter((c) => c.verdict === "possible_contributor") : [];
  const unknowns = hasChecks
    ? checks.filter((c) => !["confirmed", "possible_contributor"].includes(c.verdict))
    : [];

  function list(items, emptyLabel) {
    if (items.length === 0) return <p className="mt-1.5 text-xs text-foreground-subtle">{emptyLabel}</p>;
    return (
      <ul className="mt-1.5 space-y-1">
        {items.map((c) => (
          <li key={c.source} className="flex items-start gap-1.5 text-xs">
            <Badge tone={ROOT_CAUSE_VERDICT_TONE[c.verdict] ?? "neutral"} className="flex-shrink-0">
              {ROOT_CAUSE_VERDICT_LABEL[c.verdict] ?? "Unknown"}
            </Badge>
            <span className="text-foreground-subtle">
              {c.detail}
              {c.total_delta != null && (
                <> ({formatByUnit(Math.abs(c.total_delta), c.total_delta_unit)} total move.)</>
              )}
            </span>
          </li>
        ))}
      </ul>
    );
  }

  if (!hasChecks) {
    return <p className="text-sm text-foreground-subtle">No additional explanatory checks recorded.</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Supported contribution or concentration</span>
        {list(confirmed, "None confirmed.")}
      </div>
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Possible explanations</span>
        {list(possible, "None identified.")}
      </div>
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">Unknown causes &amp; limitations</span>
        {list(unknowns, "None recorded.")}
      </div>
    </div>
  );
}

/** Hides a raw ID or a long policy explanation behind a native <details>
 * disclosure, collapsed by default -- Celeste Investigate Phase 2 item 8:
 * "hide raw technical IDs and lengthy observation-policy explanations
 * behind expandable technical details" without removing the audit
 * record itself (it's still in the DOM, just collapsed). Moved here from
 * CaseDetail.jsx (Incident Investigation Phase 5) so StockCountInvestigation
 * Panel's session-detail view can reuse it without a circular import. */
export function TechnicalDetails({ summary, children }) {
  return (
    <details className="mt-1.5 text-xs text-foreground-subtle">
      <summary className="cursor-pointer select-none">{summary}</summary>
      <div className="mt-1.5">{children}</div>
    </details>
  );
}

/** 1. Compact case-status panel -- title, domain, severity, status, last
 * observed, secondary dismiss/reopen action. Pure presentation; the
 * dismiss/reopen action itself stays owned by the caller (CaseDetail
 * already has ConsequentialActionPanel wired to real endpoints). */
export function CaseStatusPanel({ title, domainLabel, statusBadge, severityBadge, lastObservedLabel, actionSlot }) {
  return (
    <div className="case-detail-heading flex flex-wrap items-start justify-between gap-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          {statusBadge}
          {severityBadge}
          <span className="text-xs text-foreground-subtle">{domainLabel}</span>
        </div>
        <h1 className="mt-2 font-display text-2xl font-bold text-foreground-strong">{title}</h1>
        {lastObservedLabel && <p className="mt-1 text-xs text-foreground-subtle">{lastObservedLabel}</p>}
      </div>
      {actionSlot && <div className="flex flex-none gap-2">{actionSlot}</div>}
    </div>
  );
}
