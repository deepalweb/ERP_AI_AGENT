import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowRight, Search } from "lucide-react";
import { fetchCases, fetchManagementSummary } from "../api";
import Badge, { toneForPriority } from "../design/Badge";
import Button from "../design/Button";
import { caseStatusMeta } from "../design/caseStatus";
import { DOMAIN_LABEL, DOMAIN_ORDER } from "../design/domains";
import { confidenceLabelFor, SEVERITY_TAG_META } from "../design/SignalCard";
import { RESOLUTION_VERDICT_META } from "../design/outcomeStatus";
import { PageHeading } from "../design/Management";
import { formatByUnit, formatNumber, formatPct, moveToneClass, prettifyMetricId } from "../format";

// Next step is derived purely from the case's own status -- no invented
// priority score, matching CASE_STATUS_META's real enum (design/caseStatus.js).
const NEXT_STEP_TEXT = {
  open: "Investigate evidence",
  verification_pending: "Awaiting verification",
  reopened: "Re-investigate",
  resolved: "Review outcome",
  dismissed: "—",
};

function HeroCounts({ query }) {
  const cases = query.data?.status === "ok" ? query.data.cases : null;
  if (!cases) return null;
  const items = [
    { label: "Open", value: cases.open },
    { label: "Verification pending", value: cases.verification_pending },
    { label: "Resolved", value: cases.resolved },
  ];
  return (
    <div className="mb-4 flex flex-wrap gap-4">
      {items.map((i) => (
        <div key={i.label} className="rounded-xl border border-border bg-surface px-4 py-3">
          <div className="text-2xl font-bold tabular-nums text-foreground-strong">{formatNumber(i.value ?? 0)}</div>
          <div className="text-xs text-foreground-subtle">{i.label}</div>
        </div>
      ))}
    </div>
  );
}

const PAGE_SIZE = 20;

// Every status the backend can actually return, in the order a manager
// should scan them: still-active first, then closed. No invented "needs
// attention" score -- this is just the real status vocabulary.
const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "verification_pending", label: "Verification pending" },
  { value: "reopened", label: "Reopened" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

// Reuses design/domains.js's own DOMAIN_LABEL/DOMAIN_ORDER (the same map
// Home.jsx's findings feed groups by) rather than a second domain list.
const DOMAIN_FILTERS = [{ value: "", label: "All domains" }, ...DOMAIN_ORDER.map((d) => ({ value: d, label: DOMAIN_LABEL[d] }))];

// Real priority values Badge/toneForPriority already recognize elsewhere
// (case_engine's own critical/high/medium/low ranking) -- not a new scale.
const PRIORITY_FILTERS = [
  { value: "", label: "All priorities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

// Matches case_engine.SORT_CLAUSES exactly -- a whitelist on both ends,
// never a free-text sort field sent to the backend.
const SORT_OPTIONS = [
  { value: "recent_change", label: "Recently changed" },
  { value: "priority", label: "Highest priority" },
  { value: "impact", label: "Highest impact" },
  { value: "newest", label: "Newest" },
  { value: "oldest_unresolved", label: "Oldest" },
];

// "Case type" -- the analysis kind (trend/control/incident/snapshot/
// opportunity), a different axis than domain (which area of the business).
// Derived purely from real fields already on every case, using the exact
// same routing precedent CaseDetail.jsx already established for its panel
// selection (metric_id prefix for incidents, domain for controls) -- not a
// new classification invented here. category only ever takes 'critical' or
// 'opportunity' (case_engine.py), horizon only 'monthly' or 'snapshot'.
const CASE_TYPE_TONE = { opportunity: "good", incident: "critical", control: "warning", trend: "neutral", snapshot: "neutral" };
function caseTypeFor(item) {
  if (item.category === "opportunity") return "opportunity";
  if (typeof item.metric_id === "string" && item.metric_id.startsWith("incident_")) return "incident";
  if (item.domain === "controls") return "control";
  return item.horizon === "monthly" ? "trend" : "snapshot";
}

function CaseRow({ item }) {
  const statusMeta = caseStatusMeta(item.status);
  const severityMeta = SEVERITY_TAG_META[item.priority] ?? null;
  // resolution_verdict is a stronger claim than status alone (a critical
  // case that recovers and an opportunity that captures its objective
  // both reach status="resolved" -- see design/outcomeStatus.js) -- shown
  // alongside status, not instead of it, only once the case has one.
  const verdictMeta = item.resolution_verdict ? RESOLUTION_VERDICT_META[item.resolution_verdict] : null;
  const confirm = item.latest?.confirm;
  const title = item.latest?.title ?? prettifyMetricId(item.metric_id);
  const lastSeen = item.last_seen_at ? new Date(item.last_seen_at.endsWith("Z") ? item.last_seen_at : `${item.last_seen_at}Z`) : null;
  const openedAt = item.opened_at ? new Date(item.opened_at.endsWith("Z") ? item.opened_at : `${item.opened_at}Z`) : null;
  const topContributor = item.latest?.top_contributor;
  // Deliberately NOT derived from item.action.status -- that's only the
  // most recently *updated* action, which can read "completed" while an
  // older action on the same case is still open. has_outstanding_action is
  // a separate, correct aggregate over every action (see main.py's
  // list_cases route / finding_actions.has_outstanding_action_for_cases).
  const hasOutstandingAction = Boolean(item.has_outstanding_action);
  const caseType = caseTypeFor(item);

  return (
    <Link
      to={`/investigate/${encodeURIComponent(item.case_id)}`}
      className="investigation-row flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={CASE_TYPE_TONE[caseType]}>{caseType.toUpperCase()}</Badge>
          <Badge tone={statusMeta.tone}>{statusMeta.label}</Badge>
          {verdictMeta && <Badge tone={verdictMeta.tone}>{verdictMeta.label}</Badge>}
          {severityMeta && <Badge tone={toneForPriority(item.priority)}>{severityMeta.label}</Badge>}
          <span className="text-xs text-foreground-subtle">{DOMAIN_LABEL[item.domain] ?? prettifyMetricId(item.domain)}</span>
        </div>
        <p className="mt-1.5 truncate font-display text-base font-semibold text-foreground-strong" title={title}>
          {title}
        </p>
        {confirm && (
          <p className="mt-1 text-sm tabular-nums text-foreground-subtle">
            <span className="text-foreground-strong">{formatByUnit(confirm.current, confirm.unit ?? item.latest?.metric?.unit)}</span>
            {/* A snapshot finding (no baseline/pct_change -- e.g. a stock-out
                count) has nothing to compare against; showing a bare "n/a"
                next to the value reads as broken, so it's omitted rather
                than rendered -- same fix as CaseDetail's "1n/a vs baseline". */}
            {(confirm.baseline != null || confirm.pct_change != null) && (
              <>
                {" "}
                <span className={moveToneClass(confirm.pct_change, item.latest?.metric?.direction)}>{formatPct(confirm.pct_change)}</span>
              </>
            )}
          </p>
        )}
      </div>

      {/* Previously this space was dead -- the row was two islands (left
          info, right metadata) with a large empty middle. Evidence summary
          and the top contributor were already fetched/rendered, just
          stacked under the title where they got lost; giving them their own
          column uses that space and answers "what do we know, and where"
          without opening the case. */}
      <div className="min-w-0 sm:w-56 sm:flex-none">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-foreground-subtle">Evidence</div>
        <p className="mt-1 text-xs text-foreground-subtle">
          {confidenceLabelFor(item.latest?.root_cause) ?? "No contributor has been established yet"}
          {hasOutstandingAction && <span className="ml-1.5 font-medium text-warning">· Action outstanding</span>}
        </p>
        {topContributor?.name && (
          <p className="mt-1 text-xs text-foreground-subtle">
            Main movement: <span className="text-foreground">{topContributor.name}</span>
            {topContributor.value != null && ` · ${formatNumber(topContributor.value)}`}
          </p>
        )}
      </div>

      <div className="flex flex-none items-center gap-4 sm:flex-col sm:items-end sm:gap-2">
        {item.business_impact_score != null && (
          <span className="text-xs text-foreground-subtle">
            Impact score <span className="font-semibold text-foreground-strong">{Number(item.business_impact_score).toFixed(1)}</span>
          </span>
        )}
        <span className="text-xs font-medium text-foreground">{NEXT_STEP_TEXT[item.status] ?? "Review"}</span>
        <span className="text-xs text-foreground-subtle">
          {openedAt ? `Opened ${openedAt.toLocaleDateString()}` : null}
        </span>
        <span className="text-xs text-foreground-subtle">
          {lastSeen ? `Last observed ${lastSeen.toLocaleDateString()}` : "Not yet observed"}
        </span>
        <ArrowRight className="hidden h-4 w-4 flex-none text-foreground-subtle sm:block" strokeWidth={2} />
      </div>
    </Link>
  );
}

export default function Investigate() {
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [priority, setPriority] = useState("");
  const [sort, setSort] = useState("recent_change");
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: ["cases", status, domain, priority, sort, offset],
    queryFn: () =>
      fetchCases({
        status: status || undefined,
        domain: domain || undefined,
        priority: priority || undefined,
        sort,
        limit: PAGE_SIZE,
        offset,
      }),
  });
  const summaryQuery = useQuery({ queryKey: ["management", "summary"], queryFn: fetchManagementSummary });

  function changeFilter(setter, value) {
    setter(value);
    setOffset(0);
  }

  return (
    <div className="workspace-page">
      <PageHeading title="Investigate" description="Understand the change. Review the evidence. Decide what happens next." />

      <HeroCounts query={summaryQuery} />

      <div className="filter-bar">
        <div className="filter-tabs">
          {STATUS_FILTERS.map((f) => (
            <Button key={f.value} size="sm" active={status === f.value} onClick={() => changeFilter(setStatus, f.value)}>
              {f.label}
            </Button>
          ))}
        </div>
        <select
          aria-label="Case domain"
          value={domain}
          onChange={(e) => changeFilter(setDomain, e.target.value)}
          className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
        >
          {DOMAIN_FILTERS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Case priority"
          value={priority}
          onChange={(e) => changeFilter(setPriority, e.target.value)}
          className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
        >
          {PRIORITY_FILTERS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Sort"
          value={sort}
          onChange={(e) => changeFilter(setSort, e.target.value)}
          className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
        >
          {SORT_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="investigation-list">
        {query.isLoading && (
          <>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-24 animate-pulse rounded-xl border border-border bg-surface" />
            ))}
          </>
        )}

        {query.isError || query.data?.status === "error" ? (
          <div className="flex items-center gap-2 rounded-xl border border-critical/25 bg-critical/10 p-4 text-sm text-critical">
            <AlertCircle className="h-4 w-4 flex-none" strokeWidth={2} />
            Couldn&rsquo;t load cases. {query.data?.detail || "Please try again."}
          </div>
        ) : null}

        {query.data?.status === "ok" && query.data.items.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-border bg-surface p-10 text-center">
            <Search className="h-5 w-5 text-foreground-subtle" strokeWidth={2} />
            <p className="text-sm text-foreground-subtle">
              {status || domain ? "No cases match this filter." : "No business cases yet — they appear here once the daily scan opens one."}
            </p>
          </div>
        )}

        {query.data?.status === "ok" && query.data.items.map((item) => <CaseRow key={item.case_id} item={item} />)}
      </div>

      {query.data?.status === "ok" && query.data.total > PAGE_SIZE && (
        <div className="mt-5 flex items-center justify-between text-xs text-foreground-subtle">
          <span>
            {offset + 1}&ndash;{Math.min(offset + PAGE_SIZE, query.data.total)} of {query.data.total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </Button>
            <Button size="sm" disabled={offset + PAGE_SIZE >= query.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
