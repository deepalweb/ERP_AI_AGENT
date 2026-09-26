import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowRight, Search } from "lucide-react";
import { fetchCases, fetchManagementSummary } from "../api";
import Badge, { toneForPriority } from "../design/Badge";
import Button from "../design/Button";
import { caseStatusMeta } from "../design/caseStatus";
import { DOMAIN_LABEL, DOMAIN_META } from "../design/domains";
import { formatByUnit, formatCurrency, formatNumber, formatPct, moveToneClass, prettifyMetricId } from "../format";

const PAGE_SIZE = 20;

// Every status the backend can actually return, in the order a manager
// should scan them: still-active first, then closed. No invented "needs
// attention" score -- this is just the real status vocabulary.
const STATUS_FILTERS = [
  { value: "", label: "All statuses" },
  { value: "open", label: "Open" },
  { value: "verification_pending", label: "Verification pending" },
  { value: "reopened", label: "Reopened" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

// Reuses design/domains.js's own DOMAIN_LABEL/DOMAIN_ORDER (the same map
// Home.jsx's findings feed groups by) rather than a second domain list.
const DOMAIN_FILTERS = [{ value: "", label: "All domains" }, ...Object.keys(DOMAIN_META).map((d) => ({ value: d, label: DOMAIN_LABEL[d] }))];

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
const CASE_TYPE_TONE = { opportunity: "good", incident: "critical", control: "warning", trend: "accent", snapshot: "neutral" };
function caseTypeFor(item) {
  if (item.category === "opportunity") return "opportunity";
  if (typeof item.metric_id === "string" && item.metric_id.startsWith("incident_")) return "incident";
  if (item.domain === "controls") return "control";
  return item.horizon === "monthly" ? "trend" : "snapshot";
}
const TYPE_FILTERS = [
  { value: "", label: "All types" },
  { value: "trend", label: "Trend" },
  { value: "snapshot", label: "Snapshot" },
  { value: "incident", label: "Incident" },
  { value: "control", label: "Control" },
  { value: "opportunity", label: "Opportunity" },
];

// Primary-measurement label per metric -- a monthly trend finding carries
// its own `metric.label` (see intelligence.py's MetricDef), but the 6
// controls/inventory snapshot finding shapes don't have a `metric` object
// at all (case_engine.py's _summarize_snapshot only sets it for trend
// findings), so this fills exactly that gap rather than falling back to a
// raw, unlabeled count -- see Investigate V3's "never a bare 7/54/1" rule.
const PRIMARY_LABEL = {
  negative_stock_control: "Products with negative stock",
  cost_coverage_control: "Products with no recorded cost",
  stuck_stock_count: "Unfinished stock counts",
  completed_stock_count_variance: "Stock counts with variance",
  top_seller_stock: "Top sellers out of stock",
  stock_availability: "Product-store stockouts",
};
function primaryLabelFor(item) {
  return item.latest?.metric?.label ?? PRIMARY_LABEL[item.metric_id] ?? prettifyMetricId(item.metric_id);
}

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

function EntityLine({ item }) {
  const tc = item.latest?.top_contributor;
  if (!tc?.name) return null;
  // Only surfaced as a single "affected entity" anchor when it genuinely
  // reads as the finding's one real subject -- a real SKU (top_seller_
  // stock's out-of-stock product), or the finding's own count is exactly
  // 1 so the top contributor IS the whole finding, not just the largest
  // of many (e.g. cost_coverage_control's "top" of 7 uncosted products
  // isn't a meaningful single entity -- the count already says what
  // matters there).
  if (!tc.sku && item.latest?.confirm?.current !== 1) return null;
  return (
    <p className="mt-2 text-xs text-foreground-subtle">
      {tc.name}
      {tc.sku && ` · SKU ${tc.sku}`}
    </p>
  );
}

function PrimaryMeasurement({ item }) {
  const confirm = item.latest?.confirm;
  if (!confirm || confirm.current == null) return null;
  const impact = item.latest?.impact_summary;
  const coveragePct = item.latest?.coverage_pct;
  return (
    <div className="mt-3">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-foreground-subtle">{primaryLabelFor(item)}</div>
      <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-display text-lg font-semibold tabular-nums text-foreground-strong">
          {formatByUnit(confirm.current, confirm.unit)}
        </span>
        {confirm.pct_change != null && (
          <span className={`text-xs font-medium ${moveToneClass(confirm.pct_change, item.latest?.metric?.direction)}`}>
            {formatPct(confirm.pct_change)} vs usual range
          </span>
        )}
      </div>
      {impact?.financial_impact_rs != null && (
        <p className="mt-1 text-xs text-foreground-subtle">{formatCurrency(impact.financial_impact_rs)} recorded value involved</p>
      )}
      {coveragePct != null && <p className="mt-1 text-xs text-foreground-subtle">{coveragePct}% overall coverage</p>}
    </div>
  );
}

function EvidenceBlock({ item }) {
  const ev = item.latest?.evidence_summary;
  // Never an empty "Possible explanations: None." -- omit the whole block
  // rather than render a section with nothing established or unknown.
  if (!ev?.established && !ev?.unknown) return null;
  return (
    <div className="mt-3 space-y-2 border-t border-border pt-3">
      {ev.established && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-good">What we know</div>
          <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-foreground-subtle">{ev.established}</p>
        </div>
      )}
      {ev.unknown && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-foreground-subtle">What we don&rsquo;t know</div>
          <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-foreground-subtle">{ev.unknown}</p>
        </div>
      )}
    </div>
  );
}

// The whole card is the click target (point 11 of the redesign spec) --
// "Open investigation" in the footer is a visual affordance, not a second,
// separate control.
function CaseCard({ item }) {
  const statusMeta = caseStatusMeta(item.status);
  const caseType = caseTypeFor(item);
  const title = item.latest?.title ?? prettifyMetricId(item.metric_id);
  const lastSeen = stamp(item.last_seen_at);

  return (
    <Link
      to={`/investigate/${encodeURIComponent(item.case_id)}`}
      className="group flex flex-col rounded-xl border border-border bg-surface p-4 transition hover:border-accent-border hover:bg-surface-sunken hover:shadow-[var(--shadow)]"
    >
      <div className="flex items-center justify-between gap-2">
        <Badge tone={CASE_TYPE_TONE[caseType]}>{caseType.toUpperCase()}</Badge>
        {item.priority && <Badge tone={toneForPriority(item.priority)}>{prettifyMetricId(item.priority)}</Badge>}
      </div>
      <p className="mt-2.5 text-xs text-foreground-subtle">{DOMAIN_META[item.domain]?.label ?? DOMAIN_LABEL[item.domain] ?? prettifyMetricId(item.domain)}</p>
      <p className="mt-1 font-display text-base font-semibold leading-snug text-foreground-strong">{title}</p>

      <PrimaryMeasurement item={item} />
      <EntityLine item={item} />
      <EvidenceBlock item={item} />

      <div className="mt-3 flex items-center justify-between gap-3 border-t border-border pt-3 text-xs text-foreground-subtle">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate">
            {statusMeta.label}
            {lastSeen && ` · Last observed ${lastSeen.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`}
          </span>
          {item.business_impact_score != null && (
            <span
              className="flex-none cursor-help text-foreground-subtle/70"
              title={`Priority score ${Number(item.business_impact_score).toFixed(1)}`}
            >
              ⓘ
            </span>
          )}
        </span>
        <span className="inline-flex flex-none items-center gap-1 font-medium text-accent">
          Open investigation <ArrowRight className="h-3.5 w-3.5" strokeWidth={2} />
        </span>
      </div>
    </Link>
  );
}

// A compact, fixed priority queue -- the 3-4 cases that most need a
// manager's attention right now, ahead of the full filterable library.
// Independent of the toolbar's filters (it's an orientation aid, not
// another filtered view): always the highest-priority open cases,
// regardless of what the library below is currently showing.
function NeedsAttentionRow({ item }) {
  const title = item.latest?.title ?? prettifyMetricId(item.metric_id);
  return (
    <Link
      to={`/investigate/${encodeURIComponent(item.case_id)}`}
      className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm transition hover:border-accent-border hover:bg-surface-sunken"
    >
      <Badge tone={toneForPriority(item.priority)} className="flex-none">
        {prettifyMetricId(item.priority)}
      </Badge>
      <span className="min-w-0 flex-1 truncate text-foreground-strong">{title}</span>
      <span className="flex-none text-xs text-foreground-subtle">{DOMAIN_LABEL[item.domain] ?? prettifyMetricId(item.domain)}</span>
      <ArrowRight className="h-3.5 w-3.5 flex-none text-foreground-subtle" strokeWidth={2} />
    </Link>
  );
}

function NeedsAttentionFirst() {
  const query = useQuery({
    queryKey: ["cases", "needsAttention"],
    // SORT_CLAUSES["priority"] already orders critical -> high -> medium ->
    // low -> (worth_pursuing/other last), so opportunities naturally sort
    // to the back without a separate category filter -- this is real
    // priority order, not a client-side re-ranking.
    queryFn: () => fetchCases({ status: "open", sort: "priority", limit: 4 }),
  });
  const items = query.data?.status === "ok" ? query.data.items : [];
  if (!items.length) return null;
  return (
    <div className="mb-6">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-foreground-subtle">Needs attention first</div>
      <div className="flex flex-col gap-2">
        {items.map((item) => (
          <NeedsAttentionRow key={item.case_id} item={item} />
        ))}
      </div>
    </div>
  );
}

export default function Investigate() {
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [priority, setPriority] = useState("");
  const [type, setType] = useState("");
  const [sort, setSort] = useState("recent_change");
  const [search, setSearch] = useState("");
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
  const cases = summaryQuery.data?.status === "ok" ? summaryQuery.data.cases : null;
  const priorities = summaryQuery.data?.status === "ok" ? summaryQuery.data.case_priorities : null;

  function changeFilter(setter, value) {
    setter(value);
    setOffset(0);
  }

  // Stable-referenced (not a fresh [] every render) so the useMemo hooks
  // below it actually memoize instead of recomputing every render.
  const allItems = useMemo(() => (query.data?.status === "ok" ? query.data.items : []), [query.data]);
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

  const latestSeen = useMemo(() => {
    const dates = allItems.map((item) => stamp(item.last_seen_at)).filter(Boolean);
    if (!dates.length) return null;
    return new Date(Math.max(...dates.map((d) => d.getTime())));
  }, [allItems]);

  return (
    <div className="workspace-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-foreground-strong">Investigate</h1>
          <p className="mt-1 max-w-xl text-sm text-foreground-subtle">
            Understand what changed, what the evidence establishes, and what management needs to decide.
          </p>
        </div>
        <div className="text-right text-xs text-foreground-subtle">
          {cases && <div>{formatNumber(cases.open ?? 0)} open case{(cases.open ?? 0) === 1 ? "" : "s"}</div>}
          {latestSeen && (
            <div className="mt-0.5">Cases current as of {latestSeen.toLocaleDateString(undefined, { month: "short", day: "numeric" })}</div>
          )}
        </div>
      </div>

      {(cases || priorities) && (
        <div className="mt-4 flex flex-wrap items-center gap-4 text-sm">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-foreground-subtle" />
            Open <strong className="text-foreground-strong">{formatNumber(cases?.open ?? 0)}</strong>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-critical" />
            Critical <strong className="text-foreground-strong">{formatNumber(priorities?.critical ?? 0)}</strong>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-warning" />
            High <strong className="text-foreground-strong">{formatNumber(priorities?.high ?? 0)}</strong>
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Verification <strong className="text-foreground-strong">{formatNumber(cases?.verification_pending ?? 0)}</strong>
          </span>
        </div>
      )}

      <NeedsAttentionFirst />

      <div className="sticky top-0 z-10 -mx-6 mb-5 border-b border-border bg-background px-6 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-foreground-subtle" strokeWidth={2} />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search cases..."
              aria-label="Search cases"
              className="w-full rounded-lg border border-border bg-surface py-1.5 pl-8 pr-3 text-xs text-foreground-strong outline-none placeholder:text-foreground-subtle focus:border-accent-border"
            />
          </div>
          <select
            aria-label="Case status"
            value={status}
            onChange={(e) => changeFilter(setStatus, e.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
          >
            {STATUS_FILTERS.map((f) => (
              <option key={f.value} value={f.value}>{f.label}</option>
            ))}
          </select>
          <select
            aria-label="Case domain"
            value={domain}
            onChange={(e) => changeFilter(setDomain, e.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
          >
            {DOMAIN_FILTERS.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
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
            onChange={(e) => changeFilter(setPriority, e.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
          >
            {PRIORITY_FILTERS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
          <select
            aria-label="Sort"
            value={sort}
            onChange={(e) => changeFilter(setSort, e.target.value)}
            className="ml-auto rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground-strong outline-none focus:border-accent-border"
          >
            {SORT_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>
      </div>

      {query.isLoading && (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
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
            {status || domain || priority ? "No cases match this filter." : "No business cases yet — they appear here once the daily scan opens one."}
          </p>
        </div>
      )}

      {query.data?.status === "ok" && allItems.length > 0 && items.length === 0 && (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-border bg-surface p-10 text-center">
          <Search className="h-5 w-5 text-foreground-subtle" strokeWidth={2} />
          <p className="text-sm text-foreground-subtle">No cases on this page match &ldquo;{search}&rdquo;{type && ` and type ${type}`}.</p>
        </div>
      )}

      {items.length > 0 && (
        <>
          <div className="mb-3 flex items-center justify-between text-xs text-foreground-subtle">
            <span>All cases</span>
            <span>{filtered ? `${items.length} of ${allItems.length}` : `${query.data.total} case${query.data.total === 1 ? "" : "s"}`}</span>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {items.map((item) => (
              <CaseCard key={item.case_id} item={item} />
            ))}
          </div>
        </>
      )}

      {!filtered && query.data?.status === "ok" && query.data.total > PAGE_SIZE && (
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
