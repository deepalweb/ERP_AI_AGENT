import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Search, X } from "lucide-react";
import { fetchCases, fetchManagementFamilies, fetchManagementSummary, refreshBrief } from "../api";
import Button from "../design/Button";
import InvestigationDomains from "../design/InvestigationDomains";
import { ManagementOverview, ScopeAttention, ScopeTiles } from "../design/ScopeSummary";
import IssuesTable from "../design/IssuesTable";
import DataSyncBar from "../design/DataSyncBar";
import CurrentPeriodInvestigations from "../design/CurrentPeriodInvestigations";
import { TYPE_FILTERS, caseTypeFor } from "../design/caseTypes";
import { TYPE_LABELS } from "../design/inventoryDisplay";
import { DOMAIN_LABEL, DOMAIN_META } from "../design/domains";
import { formatNumber, prettifyMetricId } from "../format";

const PAGE_SIZE = 50;
// A family is a few findings inside the inventory domain, so the family view fetches the
// whole inventory page once and filters it on the client, rather than paging by family.
const FAMILY_FETCH_LIMIT = 200;

// Every status the backend can actually return, in the order a manager
// should scan them: still-active first, then closed. No invented "needs
// attention" score -- this is just the real status vocabulary.
const STATUS_FILTERS = [
  { value: "active", label: "Active (open, reopened, verifying)" },
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "verification_pending", label: "Verification pending" },
  { value: "reopened", label: "Reopened" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

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

// Reuses design/domains.js's own labels (the same map every page reads through).
const DOMAIN_FILTERS = [{ value: "", label: "All domains" }, ...Object.keys(DOMAIN_META).map((d) => ({ value: d, label: DOMAIN_LABEL[d] }))];

function stamp(value) {
  if (!value) return null;
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

// A flat, lowercased haystack per case -- the only thing the client-side
// search box matches against. Backend search would need a real indexed
// title column (business_cases has no title/text column today, only
// metric_id/domain -- title only exists inside each day's snapshot JSON);
// out of scope for this pass, and today's case volume (well under one
// page) makes a client-side filter genuinely sufficient, not just a
// placeholder. Revisit if case volume grows past one page.
function searchHaystack(item) {
  return [item.latest?.title, DOMAIN_LABEL[item.domain], item.metric_id, item.latest?.top_contributor?.name]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

// lockedDomain: renders this same workspace pinned to one business area --
// no domain picker, no "All domains" escape hatch -- for a dedicated
// per-domain page (e.g. routes/Inventory.jsx) that reuses every piece of
// this module (family drill-down, filters, case list, early signals)
// rather than forking a second copy of this logic. The generic /investigate
// route renders this with no props, unchanged from before.
export default function Investigate({ lockedDomain, title = "Investigate", description = "Find the issues that need attention, understand where they are concentrated, and open a case to investigate further." }) {
  // ?metric=<id> -- last-resort fallback link from Management.jsx's
  // WorkItem, for an action whose case couldn't be resolved server-side.
  // Read once on mount only: a manager clearing the chip below shouldn't
  // have it silently reappear from a stale URL on the next render.
  const [searchParams, setSearchParams] = useSearchParams();
  const [metricId, setMetricId] = useState(() => searchParams.get("metric") || "");
  // Open by default (2026-10-01 decision: surface the in-progress period everywhere,
  // not hidden behind a toggle) -- the user can still collapse it manually.
  const [showSignals, setShowSignals] = useState(true);
  const [status, setStatus] = useState("active");
  const [domain, setDomain] = useState(lockedDomain ?? "");
  const [priority, setPriority] = useState("");
  const [type, setType] = useState("");
  const [sort, setSort] = useState("recent_change");
  const [search, setSearch] = useState("");
  const [familyKey, setFamilyKey] = useState("");
  const [offset, setOffset] = useState(0);

  // One place for every way the page's filters change. Changing the business
  // area resets the narrower filters, so a scope change always starts clean.
  // A locked page has no UI that ever calls applyFilters({domain: ...}), but
  // this guard keeps that true even if one's added later by mistake.
  function applyFilters(next) {
    if ("domain" in next && !lockedDomain) {
      setDomain(next.domain);
      setStatus("active");
      setPriority("");
      setType("");
      setSearch("");
      setFamilyKey("");
    }
    if ("status" in next) setStatus(next.status);
    if ("priority" in next) setPriority(next.priority);
    if ("sort" in next) setSort(next.sort);
    if ("familyKey" in next) setFamilyKey(next.familyKey);
    setOffset(0);
  }

  function clearMetricFilter() {
    setMetricId("");
    setOffset(0);
    setSearchParams((params) => {
      params.delete("metric");
      return params;
    });
  }

  const familyMode = Boolean(familyKey);
  const query = useQuery({
    queryKey: ["cases", status, domain, priority, metricId, sort, offset, familyMode],
    queryFn: () =>
      fetchCases({
        status: status || undefined,
        domain: familyMode ? "inventory" : domain || undefined,
        priority: priority || undefined,
        metric_id: metricId || undefined,
        sort,
        limit: familyMode ? FAMILY_FETCH_LIMIT : PAGE_SIZE,
        offset: familyMode ? 0 : offset,
      }),
  });
  const queryClient = useQueryClient();
  const [requested, setRequested] = useState(false);
  const summaryQuery = useQuery({
    queryKey: ["management", "summary"],
    queryFn: fetchManagementSummary,
    refetchInterval: (q) => (requested || q.state.data?.sync?.refresh?.status === "running" ? 5000 : false),
  });
  const refreshStatus = summaryQuery.data?.sync?.refresh?.status;
  const scanRunning = requested || refreshStatus === "running";
  useEffect(() => {
    if (requested && refreshStatus && refreshStatus !== "running") {
      setRequested(false);
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["management"] });
    }
  }, [requested, refreshStatus, queryClient]);
  async function runInvestigation() {
    setRequested(true);
    try { await refreshBrief(); } catch { setRequested(false); }
    summaryQuery.refetch();
  }
  const familiesQuery = useQuery({ queryKey: ["management", "families"], queryFn: fetchManagementFamilies });
  const cases = summaryQuery.data?.status === "ok" ? summaryQuery.data.cases : null;
  const families = (familiesQuery.data?.status === "ok" ? familiesQuery.data.families : null) ?? [];
  const selectedFamily = families.find((family) => family.key === familyKey) ?? null;

  // Stable-referenced (not a fresh [] every render) so the useMemo hooks
  // below it actually memoize instead of recomputing every render.
  const fetchedItems = useMemo(() => (query.data?.status === "ok" ? query.data.items : []), [query.data]);

  // Management families, from the families endpoint: which family each case belongs to.
  const familyByCase = useMemo(() => {
    const map = new Map();
    for (const family of families) {
      for (const child of family.children) map.set(child.case_id, family.key);
    }
    return map;
  }, [families]);

  const allItems = useMemo(
    () => (familyMode ? fetchedItems.filter((item) => familyByCase.get(item.case_id) === familyKey) : fetchedItems),
    [fetchedItems, familyMode, familyByCase, familyKey],
  );

  // Type and search are frontend-only filters (case_engine.list_cases has
  // no case_type/q param -- see caseTypeFor's docstring and
  // searchHaystack's), applied on top of the server's already-paginated
  // page. Fine today since every real case fits on one page; if case
  // volume grows past PAGE_SIZE, a filter could hide matches sitting on a
  // page that was never fetched -- the honest fix then is a real backend
  // case_type/q param, not a bigger client-side fetch.
  const items = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return allItems.filter((item) => {
      if (type && caseTypeFor(item) !== type) return false;
      if (needle && !searchHaystack(item).includes(needle)) return false;
      return true;
    });
  }, [allItems, type, search]);
  const filtered = Boolean(type || search.trim());

  const familyGroups = useMemo(() => families.map((family) => ({
    key: family.key,
    label: family.label,
    priority: family.priority,
    note: family.exposure_quantified_children > 1 ? family.exposure_note : null,
  })), [families]);
  const groupedItems = useMemo(() => items.map((item) => ({ ...item, family_key: familyByCase.get(item.case_id) })), [items, familyByCase]);

  const latestSeen = useMemo(() => {
    const dates = fetchedItems.map((item) => stamp(item.last_seen_at)).filter(Boolean);
    if (!dates.length) return null;
    return new Date(Math.max(...dates.map((d) => d.getTime())));
  }, [fetchedItems]);

  const scopeLabel = domain ? DOMAIN_META[domain]?.label ?? prettifyMetricId(domain) : "All domains";
  const caseRows = summaryQuery.data?.status === "ok" ? summaryQuery.data.case_domains ?? [] : [];
  const impacts = summaryQuery.data?.status === "ok" ? summaryQuery.data.case_impacts ?? [] : [];

  return (
    <div className="workspace-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground-strong">{title}</h1>
          <p className="mt-1 max-w-xl text-sm text-foreground-subtle">{description}</p>
        </div>
        <div className="text-right text-xs text-foreground-subtle">
          {cases && <div>{formatNumber(cases.open ?? 0)} open case{(cases.open ?? 0) === 1 ? "" : "s"}</div>}
          {latestSeen && (
            <div className="mt-0.5">Latest observation on this page {latestSeen.toLocaleDateString(undefined, { month: "short", day: "numeric" })}</div>
          )}
        </div>
      </div>

      {summaryQuery.data?.status === "ok" && (
        <DataSyncBar sync={summaryQuery.data.sync} running={scanRunning} failed={refreshStatus === "failed"} onRun={runInvestigation} />
      )}

      <div className={`mt-6 grid gap-4 ${lockedDomain ? "" : "xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]"}`}>
        {!lockedDomain && (
          <section aria-label="Business areas" className="space-y-3 rounded-xl border border-border bg-surface p-4">
            <h2 className="text-sm font-semibold text-foreground-strong">Business areas</h2>
            <InvestigationDomains
              rows={summaryQuery.data?.case_domains}
              loading={summaryQuery.isPending}
              error={summaryQuery.isError || summaryQuery.data?.status === "error"}
              selected={domain}
              onSelect={(value) => applyFilters({ domain: value })}
              onRetry={() => summaryQuery.refetch()}
            />
          </section>
        )}

        {summaryQuery.data?.status === "ok" && (
          <section aria-label={`${scopeLabel} summary`} className="space-y-3 rounded-xl border border-border bg-surface p-4">
            <h2 className="text-sm font-semibold text-foreground-strong">{scopeLabel} summary</h2>
            <ScopeTiles rows={caseRows} impacts={impacts} domain={domain} onFilter={applyFilters} />
          </section>
        )}
      </div>

      {summaryQuery.data?.status === "ok" && (
        <>
          <div className="mt-8">
            <ScopeAttention rows={caseRows} impacts={impacts} domain={domain} onFilter={applyFilters} />
          </div>
          <div className="mt-8">
            <ManagementOverview
              rows={caseRows}
              impacts={impacts}
              domain={domain}
              families={families}
              familyKey={familyKey}
              onFilter={applyFilters}
            />
          </div>
        </>
      )}

      <section aria-label="All active issues" className="mt-8">
        <div>
          <h2 className="text-sm font-semibold text-foreground-strong">All active issues</h2>
          <p className="mt-1 text-xs text-foreground-subtle">Open an issue for its evidence, comparisons and actions. Filters apply to this list.</p>
        </div>

        {(metricId || familyMode) && (
          <div className="mt-3 flex flex-wrap gap-2">
            {familyMode && (
              <div className="inline-flex items-center gap-2 rounded-full border border-accent-border bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
                Inventory → {selectedFamily?.label ?? "Management family"} · {formatNumber(items.length)} finding{items.length === 1 ? "" : "s"}
                <button type="button" onClick={() => applyFilters({ familyKey: "" })} aria-label="Clear family filter" className="text-accent hover:text-accent/70">
                  <X size={12} />
                </button>
              </div>
            )}
            {metricId && (
              <div className="inline-flex items-center gap-2 rounded-full border border-accent-border bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
                Filtered to {prettifyMetricId(metricId)}
                <button type="button" onClick={clearMetricFilter} aria-label="Clear metric filter" className="text-accent hover:text-accent/70">
                  <X size={12} />
                </button>
              </div>
            )}
          </div>
        )}

        <div className="sticky top-0 z-10 -mx-[18px] mb-5 mt-3 border-b border-border bg-background px-[18px] py-3 sm:-mx-6 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-foreground-subtle" strokeWidth={2} />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search this page of cases..."
                aria-label="Search cases"
                className="w-full rounded-lg border border-border bg-surface py-1.5 pl-8 pr-3 text-xs text-foreground-strong outline-none placeholder:text-foreground-subtle focus:border-accent-border"
              />
            </div>
            <select
              aria-label="Case status"
              value={status}
              onChange={(e) => applyFilters({ status: e.target.value })}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
            >
              {STATUS_FILTERS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </select>
            {!domain && (
              <select
                aria-label="Case domain"
                value={domain}
                onChange={(e) => applyFilters({ domain: e.target.value })}
                className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
              >
                {DOMAIN_FILTERS.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            )}
            <select
              aria-label="Case type"
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
            >
              {TYPE_FILTERS.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <select
              aria-label="Case priority"
              value={priority}
              onChange={(e) => applyFilters({ priority: e.target.value })}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
            >
              {PRIORITY_FILTERS.map((p) => (
                <option key={p.value} value={p.value}>{p.label}</option>
              ))}
            </select>
            <select
              aria-label="Sort"
              value={sort}
              onChange={(e) => applyFilters({ sort: e.target.value })}
              className="ml-auto rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
            >
              {SORT_OPTIONS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
        </div>

        {query.isLoading && (
          <div className="grid grid-cols-1 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-48 animate-pulse rounded-xl border border-border bg-surface" />
            ))}
          </div>
        )}

        {query.isError || query.data?.status === "error" ? (
          <div className="flex items-center gap-2 rounded-xl border border-critical/25 bg-critical/10 p-4 text-sm text-critical">
            <AlertCircle className="h-4 w-4 flex-none" strokeWidth={2} />
            Couldn&rsquo;t load cases. {query.data?.detail || "Please try again."}
          </div>
        ) : null}

        {query.data?.status === "ok" && allItems.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-border bg-surface p-10 text-center">
            <Search className="h-5 w-5 text-foreground-subtle" strokeWidth={2} />
            <p className="text-sm text-foreground-subtle">
              {status || domain || priority || familyMode ? "No cases match this filter." : "No business cases yet — they appear here once the daily scan opens one."}
            </p>
          </div>
        )}

        {query.data?.status === "ok" && allItems.length > 0 && items.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-border bg-surface p-10 text-center">
            <Search className="h-5 w-5 text-foreground-subtle" strokeWidth={2} />
            <p className="text-sm text-foreground-subtle">No cases match &ldquo;{search}&rdquo;{type && ` and type ${type}`}.</p>
          </div>
        )}

        {items.length > 0 && (
          <>
            <div className="mb-3 flex items-center justify-between text-xs text-foreground-subtle">
              <span>{scopeLabel} · Saved investigations</span>
              <span>{filtered ? `${items.length} of ${allItems.length}` : familyMode ? `${items.length} case${items.length === 1 ? "" : "s"}` : `${query.data.total} case${query.data.total === 1 ? "" : "s"}`}</span>
            </div>
            <IssuesTable items={groupedItems} groups={familyGroups} evidenceFor={(id) => summaryQuery.data?.case_impacts?.find((row) => row.case_id === id)} typeFor={(item) => TYPE_LABELS[caseTypeFor(item)] ?? caseTypeFor(item)} />
          </>
        )}

        {!familyMode && query.data?.status === "ok" && query.data.total > PAGE_SIZE && (
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
      </section>

      <details className="mt-8 rounded-xl border border-border p-4" open={showSignals} onToggle={(event) => setShowSignals(event.currentTarget.open)}>
        <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">Early signals</summary>
        <p className="mt-2 text-xs text-foreground-subtle">Current-period measurements that have not yet become saved management cases.</p>
        {showSignals && <CurrentPeriodInvestigations metricId={metricId} domain={domain} />}
      </details>
    </div>
  );
}
