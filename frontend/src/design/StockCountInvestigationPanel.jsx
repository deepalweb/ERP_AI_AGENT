import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ObservationTrend, ContributionComparison, AffectedRecordTable, TechnicalDetails } from "./CaseVisuals";
import Button from "./Button";
import { fetchStockCountSessionDetail } from "../api";
import { formatCurrency, formatNumber } from "../format";

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
function fmtDate(value) {
  const d = toDate(value);
  return d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "n/a";
}

const SESSION_STATUS_LABEL = {
  draft: "Draft -- not yet started",
  in_progress: "In progress",
  review: "In review -- not finalized",
  done: "Completed in source system",
  cancel: "Cancelled in source system",
};

function investigationStatusFor(session) {
  return SESSION_STATUS_LABEL[session.state] ?? session.state;
}

const VALUATION_BASIS_LABEL = {
  source_cost: "Source cost",
  catalog_estimate: "Catalog cost (estimated)",
  no_cost_data: "No cost data -- excluded from total",
  scanned: "Scanned -- real count",
};

/** Visual Investigation V2 / Incident Investigation: business-specific
 * investigation panel for the stuck_stock_count finding (state_findings.
 * _stuck_stock_count_finding). Mirrors InventoryInvestigationPanel/
 * RevenueInvestigationPanel's pattern -- gated by the caller on
 * c.metric_id, reads only structured fields already carried on the case
 * (trendPoints from performance_history, breakdowns, and the sessions list
 * added specifically for this table), never scrapes recommendation prose
 * for structured data. */
export default function StockCountInvestigationPanel({ latest, trendPoints, onRetry }) {
  const [sortKey, setSortKey] = useState("value");
  const [selectedId, setSelectedId] = useState(null);

  const confirmedCount = latest?.confirm?.current;
  const sessions = latest?.sessions;
  const outletBreakdown = (latest?.breakdowns ?? []).find((b) => b.dimension === "store");

  // Incident Investigation Phase 1 root cause: an older, already-persisted
  // observation can predate the `sessions` field entirely (it was added
  // after that snapshot was captured) -- that is NOT the same claim as
  // "zero sessions." Rendering the old "No sessions recorded" text here
  // directly contradicted the aggregate finding's own count above it.
  if (sessions === undefined) {
    return (
      <div className="rounded-lg border border-warning/30 bg-warning/10 p-3.5 text-sm text-foreground">
        <p>
          Session-level detail wasn't captured for this observation (an older data format) -- the
          {" "}{confirmedCount != null ? formatNumber(confirmedCount) : "reported"} session count and Rs figure
          above still reflect what was measured at the time. A new scan will include full session detail.
        </p>
        {onRetry && <Button size="sm" className="mt-2" onClick={onRetry}>Refresh now</Button>}
      </div>
    );
  }

  if (sessions.length === 0) {
    // The aggregate finding never fires with zero sessions, so reaching
    // this with a non-zero confirmedCount means the read itself failed --
    // an explicit evidence-unavailable state with a retry, not a bare
    // "no sessions" that could be misread as "resolved."
    return (
      <div className="rounded-lg border border-critical/30 bg-critical/10 p-3.5 text-sm text-foreground">
        <p>
          Evidence unavailable -- the aggregate finding reports {confirmedCount != null ? formatNumber(confirmedCount) : "some"}
          {" "}session(s), but no session records loaded.
        </p>
        {onRetry && <Button size="sm" className="mt-2" onClick={onRetry}>Retry</Button>}
      </div>
    );
  }

  const sortedSessions = [...sessions].sort((a, b) =>
    sortKey === "age" ? b.days_stuck - a.days_stuck : b.unscanned_value_rs - a.unscanned_value_rs
  );
  const selected = sortedSessions.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="space-y-4">
      <div>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
          A. Unfinished sessions over time
        </span>
        <div className="mt-2">
          <ObservationTrend
            points={trendPoints}
            formatValue={formatNumber}
            formatX={(m) => m}
            unitLabel="unfinished sessions"
          />
        </div>
      </div>

      {outletBreakdown && (
        <div className="border-t border-border pt-3">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            B. {outletBreakdown.label}
          </span>
          <div className="mt-2">
            <ContributionComparison
              data={outletBreakdown.top_contributors}
              unitLabel="Rs estimated value of unscanned lines, by outlet -- not session count"
              valueKey="current"
              formatValue={formatCurrency}
            />
          </div>
        </div>
      )}

      <div className="border-t border-border pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-foreground-subtle">
            C. Affected count sessions ({sortedSessions.length})
          </span>
          <div className="flex gap-1 text-xs">
            <button
              type="button"
              onClick={() => setSortKey("value")}
              className={`rounded px-2 py-0.5 ${sortKey === "value" ? "bg-accent-bg text-accent" : "text-foreground-subtle hover:text-foreground"}`}
            >
              Sort by exposure
            </button>
            <button
              type="button"
              onClick={() => setSortKey("age")}
              className={`rounded px-2 py-0.5 ${sortKey === "age" ? "bg-accent-bg text-accent" : "text-foreground-subtle hover:text-foreground"}`}
            >
              Sort by age
            </button>
          </div>
        </div>
        <div className="mt-2">
          <AffectedRecordTable
            rowKey="id"
            rows={sortedSessions}
            onRowClick={(row) => setSelectedId(row.id === selectedId ? null : row.id)}
            selectedRowKey={selectedId}
            columns={[
              { key: "name", label: "Session" },
              { key: "outlet", label: "Outlet" },
              { key: "started_at", label: "Started", format: (v) => fmtDate(v) },
              { key: "responsible", label: "Responsible" },
              { key: "days_stuck", label: "Age", format: (v) => `${v.toFixed(0)} day${v === 1 ? "" : "s"}` },
              {
                key: "unscanned_lines",
                label: "Completion",
                format: (v, row) => `${row.scanned_lines} of ${row.total_lines} lines scanned`,
              },
              { key: "state", label: "Status", format: investigationStatusFor },
              { key: "unscanned_value_rs", label: "Estimated value", format: (v) => formatCurrency(v) },
              {
                key: "id",
                label: "",
                format: (v) => (
                  <span className="whitespace-nowrap text-accent">{v === selectedId ? "Hide detail" : "Investigate →"}</span>
                ),
              },
            ]}
          />
        </div>
        <p className="mt-2 text-xs text-foreground-subtle">
          Estimated value of unscanned stock-count lines -- a reporting estimate, not confirmed inventory loss or an
          accounting adjustment -- see "What the evidence establishes" below.
        </p>
      </div>

      {selected && <SessionDetailPanel session={selected} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

/** Incident Investigation Phase 5: the full individual-session record --
 * identity/state, real people and roles (explicitly distinguishing
 * responsible from record-creator), the real per-scan event timeline
 * (empty, honestly, when a session has no scan activity), count-line
 * detail, and a plain statement of what's established vs. unknown for
 * THIS session -- never inferred from write metadata (see stock_counts.
 * get_stock_count_session_detail's own docstring for why). */
function SessionDetailPanel({ session, onClose }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["stockCountSessionDetail", session.id],
    queryFn: () => fetchStockCountSessionDetail(session.id),
  });

  const detail = data?.session ?? null;

  return (
    <div className="rounded-lg border border-border bg-surface-sunken p-3.5">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-foreground-strong">Session investigation -- {session.name}</span>
        <Button size="sm" onClick={onClose}>Close</Button>
      </div>

      {isLoading && <p className="mt-2 text-sm text-foreground-subtle">Loading session detail…</p>}

      {isError && (
        <div className="mt-2 text-sm text-foreground">
          <p>Evidence unavailable -- this session's detail couldn't be loaded.</p>
          <Button size="sm" className="mt-2" onClick={() => refetch()}>Retry</Button>
        </div>
      )}

      {detail && (
        <div className="mt-3 space-y-3 text-sm">
          <p className="text-xs text-foreground-subtle">
            Current source-system data, retrieved {fmtDateTime(detail.retrieved_at)} -- distinct from the case's own
            preserved evidence shown elsewhere on this page, which reflects what was measured at the time of that
            observation and may differ from this live read.
          </p>
          {/* 1. Identity and current state. */}
          <div className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
            <div><span className="text-foreground-subtle">Outlet: </span>{detail.outlet}</div>
            <div><span className="text-foreground-subtle">Source-system status: </span>{investigationStatusFor(detail)}</div>
            <div><span className="text-foreground-subtle">Started: </span>{fmtDateTime(detail.started_at)}</div>
            <div><span className="text-foreground-subtle">Entered review: </span>{detail.review_started_at ? fmtDateTime(detail.review_started_at) : "not yet"}</div>
          </div>

          {/* 2. People and roles -- deliberately two separate fields, never
              collapsed into one "owner" -- verified live these are
              genuinely different people on real sessions. */}
          <div className="border-t border-border pt-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">People and roles</p>
            <p className="mt-1">
              <span className="text-foreground-subtle">Responsible (session owner): </span>{detail.responsible}
            </p>
            <p><span className="text-foreground-subtle">Record created by: </span>{detail.created_by}</p>
            {detail.responsible !== detail.created_by && (
              <p className="mt-1 text-xs text-foreground-subtle">
                These are different people on record -- the session's responsible owner is not necessarily who
                created it, and neither is necessarily who physically counted anything (see the event timeline below).
              </p>
            )}
            {detail.notes && (
              <p className="mt-1 text-xs text-foreground-subtle">System note: {detail.notes}</p>
            )}
          </div>

          {/* 3. Real per-scan event timeline. */}
          <div className="border-t border-border pt-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
              Operational activity ({detail.scan_events.length} recorded scan{detail.scan_events.length === 1 ? "" : "s"})
            </p>
            {detail.scan_events.length === 0 ? (
              <p className="mt-1 text-foreground-subtle">No scan activity has been recorded for this session.</p>
            ) : (
              <TechnicalDetails summary={`Show ${detail.scan_events.length} scan event(s)`}>
                <ul className="mt-1 space-y-1">
                  {detail.scan_events.map((e, i) => (
                    <li key={i}>
                      {fmtDateTime(e.scan_datetime)} -- {e.scanned_by} scanned {e.product_name} ({e.sku}): {formatNumber(e.scanned_qty)}
                      {" "}(system recorded {formatNumber(e.onhand_qty_at_scan)} at that moment)
                    </li>
                  ))}
                </ul>
              </TechnicalDetails>
            )}
          </div>

          {/* 4/5. Count-line analysis and variance. */}
          <div className="border-t border-border pt-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Count-line analysis</p>
            <p className="mt-1">
              {detail.scanned_lines} of {detail.total_lines} lines scanned -- {detail.unscanned_lines} never counted.
              Estimated value of unscanned lines: {formatCurrency(detail.unscanned_value_rs)}, valued at the source
              system's own per-line cost where recorded, falling back to catalog cost only where it isn't -- a
              reporting estimate, not confirmed inventory loss or an accounting adjustment.
            </p>
            {detail.lines.length > 0 && (
              <TechnicalDetails summary={`Show all ${detail.lines.length} count line(s)`}>
                <p className="mb-2 text-foreground-subtle">
                  "Variance value" below is the source system's own raw figure for that line and will not simply add
                  up to the "Estimated value of unscanned lines" above -- that total only sums unscanned lines,
                  floors a negative recorded quantity to zero (a data-integrity issue, not phantom stock), and
                  applies a catalog-cost estimate on a line the source system has no cost for. "Valuation basis"
                  shows which of those applies per line.
                </p>
                <AffectedRecordTable
                  rowKey="id"
                  rows={[...detail.lines].sort((a, b) => Math.abs(b.variance_value_rs) - Math.abs(a.variance_value_rs))}
                  columns={[
                    { key: "product_name", label: "Product" },
                    { key: "sku", label: "SKU" },
                    { key: "onhand_qty", label: "Recorded qty" },
                    { key: "counted_qty", label: "Counted qty" },
                    { key: "variance_value_rs", label: "Variance value", format: (v) => formatCurrency(v) },
                    { key: "valuation_basis", label: "Valuation basis", format: (v) => VALUATION_BASIS_LABEL[v] ?? v },
                    { key: "was_scanned", label: "Scanned?", format: (v) => (v ? "Yes" : "No") },
                  ]}
                />
              </TechnicalDetails>
            )}
          </div>

          {/* 6. Established vs. unknown for this session. */}
          <div className="border-t border-border pt-2">
            <p className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Established vs. unknown</p>
            <ul className="mt-1 list-inside list-disc text-foreground-subtle">
              <li>Established: source-system status, line/scan counts, and (where present) the system note above.</li>
              <li>Unknown: no assignment record beyond the session-level responsible field; no reviewer-attribution field exists in the source system; no audit/chatter log exists for this session type.</li>
            </ul>
          </div>

          {/* 7/8. Pointer to management action + disposition, kept separate. */}
          <p className="border-t border-border pt-2 text-xs text-foreground-subtle">
            Management recommendations and recorded actions for this case are shown in "Proposed by Celeste" and
            "Actions taken" below -- this session's own source-system status above is independent of the case's
            overall status.
          </p>
        </div>
      )}
    </div>
  );
}
