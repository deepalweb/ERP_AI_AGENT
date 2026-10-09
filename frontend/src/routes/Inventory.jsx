import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  fetchInventoryDepth,
  fetchInventoryKpiHistory,
  fetchStockAlerts,
  fetchTopProductStockCoverage,
  fetchStuckStockCounts,
  fetchCompletedStockCountVariance,
  fetchLossAndDamage,
  fetchCases,
} from "../api";
import Section from "../design/Card";
import Badge, { toneForPriority } from "../design/Badge";
import Pill from "../design/Pill";
import RankedBarChart from "../charts/RankedBarChart";
import OrderedBarList from "../charts/OrderedBarList";
import StackedBarChart from "../charts/StackedBarChart";
import GroupedStackedBarChart from "../charts/GroupedStackedBarChart";
import BulletBar from "../charts/BulletBar";
import Heatmap from "../charts/Heatmap";
import TrendChart from "../charts/TrendChart";
import { formatCurrency, formatDate, formatNumber, stripSkuSuffix } from "../format";

const DEPTH_COLOR = { healthy: "var(--status-good)", overstock: "var(--status-warning)", dead_stock: "var(--status-critical)" };
const SHORTAGE_REASON_COLOR = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--accent)"];

function outletShort(name) {
  return (name || "").replace("Celeste Daily", "").replace("-", "").trim();
}

function daysStuckTone(days) {
  return days >= 30 ? "critical" : days >= 7 ? "warning" : "good";
}

function coverTone(product) {
  if (product.negative_stock) return "critical";
  if (product.short) return "warning";
  return "good";
}

const STATUS_TEXT_CLASS = { good: "text-good", warning: "text-warning", critical: "text-critical" };

/** A KPI card with a directional delta against the prior day's snapshot --
 * "lower is better" for every Inventory KPI (fewer out-of-stocks, less
 * dead/overstock value, fewer stuck sessions, less shortage), so a rising
 * number is always shown in critical red, never green. Not the shared
 * charts/StatTile: that component hardcodes "vs prior month" as a
 * percentage, which is wrong for a raw count or a currency delta -- this
 * one takes its own formatDelta so "out of stock" reads "+86" and "dead
 * stock" reads "+Rs 12K", not a fabricated percentage. delta undefined (no
 * prior snapshot yet) shows no line at all, never a fake 0. */
function KpiTile({ label, value, note, status, delta, formatDelta = formatNumber }) {
  const toneClass = STATUS_TEXT_CLASS[status] ?? "text-foreground-strong";
  const hasDelta = delta !== undefined && delta !== null;
  const worse = hasDelta && delta > 0; // every KPI here is lower-is-better
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-foreground-subtle">{label}</div>
      <div className={`mt-1 text-xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {hasDelta && delta !== 0 && (
        <div className={`mt-1 text-xs font-medium tabular-nums ${worse ? "text-critical" : "text-good"}`}>
          {worse ? "↑" : "↓"} {formatDelta(Math.abs(delta))} vs previous scan
        </div>
      )}
      {hasDelta && delta === 0 && <div className="mt-1 text-xs text-foreground/50">No change vs previous scan</div>}
      {note && <div className="mt-1 text-xs text-foreground-subtle">{note}</div>}
    </div>
  );
}

function StuckStockCountsTable({ sessions }) {
  if (!sessions || sessions.length === 0) {
    return <p className="text-sm text-foreground/50">No stuck stock counts right now.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Session</th>
            <th className="pb-2 font-medium">Outlet</th>
            <th className="pb-2 font-medium">Responsible</th>
            <th className="pb-2 text-right font-medium">Days stuck</th>
            <th className="pb-2 text-right font-medium">Unscanned lines</th>
            <th className="pb-2 text-right font-medium">Value at risk</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id} className="border-t border-border">
              <td className="py-1.5 font-medium text-foreground-strong">{s.name}</td>
              <td className="py-1.5 text-foreground/80">{s.outlet}</td>
              <td className="py-1.5 text-foreground/80">{s.responsible || "Unassigned"}</td>
              <td className="py-1.5 text-right">
                <Pill tone={daysStuckTone(s.days_stuck)}>{s.days_stuck}d</Pill>
              </td>
              <td className="py-1.5 text-right">{formatNumber(s.unscanned_lines)} / {formatNumber(s.total_lines)}</td>
              <td className="py-1.5 text-right text-foreground-strong">{formatCurrency(s.unscanned_value_rs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Plain table instead of a ranked bar -- real product names here run long
 * enough to wrap onto 2-3 lines inside a bar row, which reads as clutter and
 * makes the bars themselves hard to compare. A table has no label-width
 * problem and gives the exact unit/days-of-cover numbers next to the value,
 * which a bar's label-suffix hack was standing in for anyway. */
function ExposureTable({ items }) {
  if (!items || items.length === 0) {
    return <p className="text-sm text-foreground/50">No items.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Product</th>
            <th className="pb-2 text-right font-medium">Units</th>
            <th className="pb-2 text-right font-medium">Days of cover</th>
            <th className="pb-2 pl-4 text-right font-medium">Value</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.name} className="border-t border-border">
              <td className="py-1.5 font-medium text-foreground-strong">{stripSkuSuffix(i.name)}</td>
              <td className="py-1.5 text-right">{formatNumber(i.units)}</td>
              <td className="py-1.5 text-right">{i.days_of_stock != null ? `${formatNumber(i.days_of_stock)}d` : "n/a"}</td>
              <td className="py-1.5 pl-4 text-right text-foreground-strong">{formatCurrency(i.estimated_value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Each row pairs a BulletBar (how close to the 3-day line) with the exact
 * numbers a manager needs to act -- the chart answers "how bad," the table
 * answers "which SKU, which outlet, right now." Negative stock is its own
 * "Data issue" status, never folded into an (impossible) negative days
 * figure. */
function StockCoverageTable({ data, maxDays }) {
  if (!data || !data.products || data.products.length === 0) {
    return <p className="text-sm text-foreground/50">No stock coverage data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Product</th>
            <th className="pb-2 font-medium">Days of cover</th>
            <th className="pb-2 text-right font-medium">Sold (30d)</th>
            <th className="pb-2 text-right font-medium">Current stock</th>
            <th className="pb-2 pl-4 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {data.products.map((p) => (
            <tr key={p.sku ?? p.name} className="border-t border-border">
              <td className="py-1.5">
                <div className="font-medium text-foreground-strong">{stripSkuSuffix(p.name)}</div>
                {p.sku && <div className="text-xs text-foreground/50">SKU {p.sku}</div>}
              </td>
              <td className="w-40 py-1.5 pr-4">
                {p.negative_stock ? (
                  <span className="text-xs text-critical">Data issue</span>
                ) : (
                  <div className="flex items-center gap-2">
                    <div className="w-20">
                      <BulletBar value={p.days_of_cover ?? 0} max={maxDays} threshold={data.low_stock_threshold_days} tone={coverTone(p)} />
                    </div>
                    <span className="text-xs tabular-nums text-foreground/80">{p.days_of_cover ?? "n/a"}d</span>
                  </div>
                )}
              </td>
              <td className="py-1.5 text-right">{formatNumber(p.qty_sold_30d)}</td>
              <td className="py-1.5 text-right">{formatNumber(p.current_stock)}</td>
              <td className="py-1.5 pl-4">
                {p.negative_stock ? (
                  <Pill tone="critical">Data issue</Pill>
                ) : p.short ? (
                  <Pill tone="warning">{`Under ${data.low_stock_threshold_days}d`}</Pill>
                ) : (
                  <Pill tone="good">OK</Pill>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CaseRow({ item }) {
  return (
    <Link
      to={`/investigate/${encodeURIComponent(item.case_id)}`}
      className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-sm hover:bg-background"
    >
      <span className="min-w-0 truncate text-foreground-strong">{item.latest?.title || item.metric_id}</span>
      <span className="flex flex-shrink-0 items-center gap-2">
        <Badge tone={toneForPriority(item.priority)}>{item.priority || "unranked"}</Badge>
        <span className="text-xs text-foreground/50">{formatDate(item.last_seen_at?.slice(0, 10))}</span>
      </span>
    </Link>
  );
}

export default function Inventory() {
  const depth = useQuery({ queryKey: ["inventoryDepth"], queryFn: fetchInventoryDepth });
  const kpiHistory = useQuery({ queryKey: ["inventoryKpiHistory"], queryFn: fetchInventoryKpiHistory });
  const stockAlerts = useQuery({ queryKey: ["stockAlerts"], queryFn: fetchStockAlerts });
  const stockCoverage = useQuery({
    queryKey: ["topProductStockCoverage"],
    queryFn: () => fetchTopProductStockCoverage(15, 10),
  });
  const stuckCounts = useQuery({ queryKey: ["stuckStockCounts"], queryFn: fetchStuckStockCounts });
  const variance = useQuery({ queryKey: ["completedStockCountVariance"], queryFn: () => fetchCompletedStockCountVariance() });
  const lossAndDamage = useQuery({ queryKey: ["lossAndDamage"], queryFn: fetchLossAndDamage });
  const cases = useQuery({
    queryKey: ["cases", "inventory", "active"],
    queryFn: () => fetchCases({ domain: "inventory", status: "active", sort: "priority", limit: 6 }),
  });

  const d = depth.data?.status === "ok" ? depth.data : null;
  const kpi = kpiHistory.data?.status === "ok" ? kpiHistory.data.deltas : null;
  const sa = stockAlerts.data?.status === "ok" ? stockAlerts.data : null;
  const stuck = stuckCounts.data?.status === "ok" ? stuckCounts.data : null;
  const v = variance.data?.status === "ok" ? variance.data : null;
  const lad = lossAndDamage.data?.status === "ok" ? lossAndDamage.data : null;
  const openCases = cases.data?.status === "ok" ? cases.data.items : [];

  const segByid = Object.fromEntries((d?.segments ?? []).map((s) => [s.id, s]));
  const varianceByOutlet = v?.breakdowns?.find((b) => b.dimension === "store");

  // Grouped by day (several sessions can complete the same day) so the
  // trend line shows one point per day, not several stacked at the same x.
  function dailyTrendFrom(sessions, dateKey, valueKey) {
    return Object.entries(
      sessions.reduce((byDay, s) => {
        const day = s[dateKey].slice(0, 10);
        byDay[day] = (byDay[day] ?? 0) + s[valueKey];
        return byDay;
      }, {})
    )
      .map(([date, value]) => ({ date, value }))
      .sort((a, b) => (a.date < b.date ? -1 : 1));
  }

  const varianceTrend = v?.sessions ? dailyTrendFrom(v.sessions, "completed_at", "net_variance_value_rs") : [];

  const stuckValueByOutlet = {};
  for (const s of stuck?.sessions ?? []) {
    stuckValueByOutlet[s.outlet] = (stuckValueByOutlet[s.outlet] ?? 0) + s.unscanned_value_rs;
  }
  const varianceAbsByOutlet = Object.fromEntries(
    (varianceByOutlet?.top_contributors ?? []).map((c) => [c.name, c.current])
  );

  // Outlet Risk Overview -- 4 measurements that are each a cheap, honest
  // read of real exposure (OOS rate, low-cover rate, count variance,
  // unresolved stuck-count value). "Transfer discrepancy" from the brief's
  // own suggestion deliberately left out: that signal lives in a different
  // detection domain (dispatch/transfer_discrepancy) this page doesn't
  // fetch, and guessing at it here would mean displaying a number that was
  // never actually checked against that domain's own measurement.
  const riskRows = (sa?.segments_by_outlet ?? []).map((o) => ({ id: o.outlet, label: outletShort(o.outlet) }));
  const riskColumns = [
    { id: "oos_rate", label: "OOS rate" },
    { id: "low_rate", label: "Low-cover rate" },
    { id: "variance", label: "Count variance" },
    { id: "stuck_value", label: "Stuck value" },
  ];
  const riskData = (sa?.segments_by_outlet ?? []).flatMap((o) => [
    { row: o.outlet, col: "oos_rate", value: o.out_of_stock_rate_pct },
    { row: o.outlet, col: "low_rate", value: o.low_stock_rate_pct },
    { row: o.outlet, col: "variance", value: varianceAbsByOutlet[o.outlet] ?? null },
    { row: o.outlet, col: "stuck_value", value: stuckValueByOutlet[o.outlet] ?? null },
  ]).filter((c) => c.value != null);

  const stockStateRows = (sa?.segments_by_outlet ?? []).map((o) => ({ id: o.outlet, label: outletShort(o.outlet) }));
  const stockStateColumns = [
    { id: "oos", label: "OOS" },
    { id: "low", label: "Low" },
    { id: "healthy", label: "Healthy" },
  ];
  const stockStateData = (sa?.segments_by_outlet ?? []).flatMap((o) => [
    { row: o.outlet, col: "oos", value: o.out_of_stock },
    { row: o.outlet, col: "low", value: o.low_stock },
    { row: o.outlet, col: "healthy", value: o.healthy },
  ]);

  const coverageMaxDays = Math.max(
    15,
    ...(stockCoverage.data?.status === "ok" ? stockCoverage.data.products : [])
      .map((p) => p.days_of_cover)
      .filter((d) => d != null)
  );

  const shortageReasons = [...new Set((lad?.shortage_by_reason_and_store ?? []).map((r) => r.reason))];
  const reasonByOutletData = (lad?.shortage_by_store ?? []).map((s) => {
    const row = { name: outletShort(s.outlet) };
    for (const reason of shortageReasons) {
      const match = lad.shortage_by_reason_and_store.find((r) => r.outlet === s.outlet && r.reason === reason);
      row[reason] = match?.qty ?? 0;
    }
    return row;
  });
  const reasonSeries = shortageReasons.map((reason, i) => ({
    key: reason, label: reason, color: SHORTAGE_REASON_COLOR[i % SHORTAGE_REASON_COLOR.length],
  }));

  const shortageTrend = (lad?.shortage_daily_trend ?? []).map((r) => ({ date: r.day, value: r.qty }));

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground-strong">Inventory &amp; shrinkage</h1>
          <p className="mt-1 text-sm text-foreground/60">
            Are we healthy, where are we exposed, what's running out, what's trapped, and where are losses happening.
          </p>
        </div>
        {d?.as_of_date && <span className="text-xs text-foreground/60">Data through {formatDate(d.as_of_date)}</span>}
      </div>

      {/* KPI strip */}
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <KpiTile
          label="Out of stock"
          value={sa ? formatNumber(sa.distinct_out_of_stock_products) : "--"}
          note="distinct products"
          status="critical"
          delta={kpi?.out_of_stock_products.delta}
        />
        <KpiTile
          label="Low stock"
          value={sa ? formatNumber(sa.distinct_low_stock_products) : "--"}
          note="distinct products"
          status="warning"
          delta={kpi?.low_stock_products.delta}
        />
        <KpiTile
          label="Dead stock"
          value={segByid.dead_stock ? formatCurrency(segByid.dead_stock.value) : "--"}
          note={segByid.dead_stock ? `${formatNumber(segByid.dead_stock.count)} SKUs` : undefined}
          status="critical"
          delta={kpi?.dead_stock_value.delta}
          formatDelta={formatCurrency}
        />
        <KpiTile
          label="Overstock"
          value={segByid.overstock ? formatCurrency(segByid.overstock.value) : "--"}
          note={segByid.overstock ? `${formatNumber(segByid.overstock.count)} SKUs` : undefined}
          status="warning"
          delta={kpi?.overstock_value.delta}
          formatDelta={formatCurrency}
        />
        <KpiTile
          label="Stuck stock counts"
          value={stuck ? formatNumber(stuck.sessions.length) : "--"}
          note={stuck ? `${formatCurrency(stuck.total_value_at_risk_rs)} at risk` : undefined}
          status="warning"
          delta={kpi?.stuck_count_sessions.delta}
        />
        <KpiTile
          label="Shortage qty"
          value={lad ? formatNumber(lad.shortage_by_reason.reduce((n, r) => n + r.qty, 0)) : "--"}
          note={lad?.period}
          status="critical"
          delta={kpi?.shortage_qty.delta}
        />
      </div>
      {!kpi && (kpiHistory.data?.status === "ok") && (
        <p className="mt-2 text-xs text-foreground/50">
          No prior snapshot to compare against yet -- deltas will appear once a second day&rsquo;s scan has run.
        </p>
      )}

      {/* Inventory health */}
      <Section title="Inventory health" note={d ? `${formatNumber(d.segments.reduce((n, s) => n + s.count, 0))} SKUs tracked` : undefined}>
        {depth.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {depth.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load inventory depth.</p>}
        {d && (
          <>
            <p className="text-sm text-foreground-strong">
              {segByid.healthy
                ? `${((segByid.healthy.value / d.segments.reduce((s, x) => s + x.value, 0)) * 100).toFixed(0)}% of recorded inventory value is in healthy stock positions`
                : null}
            </p>
            <div className="mt-2">
              <StackedBarChart
                segments={[
                  { name: "Healthy", value: segByid.healthy?.value ?? 0, color: DEPTH_COLOR.healthy },
                  { name: "Overstock", value: segByid.overstock?.value ?? 0, color: DEPTH_COLOR.overstock },
                  { name: "Dead stock", value: segByid.dead_stock?.value ?? 0, color: DEPTH_COLOR.dead_stock },
                ]}
                formatValue={formatCurrency}
              />
            </div>
            <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Top dead-stock exposure</h3>
                <div className="mt-2">
                  <ExposureTable items={(d.dead_stock_items ?? []).slice(0, 8)} />
                </div>
              </div>
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Top overstock exposure</h3>
                <div className="mt-2">
                  <ExposureTable items={(d.overstock_items ?? []).slice(0, 8)} />
                </div>
              </div>
            </div>
            <p className="mt-3 text-xs text-foreground/50">
              Value coverage ({segByid.healthy?.value_coverage}, {segByid.overstock?.value_coverage}, {segByid.dead_stock?.value_coverage} items
              with a real cost) means a small share of SKUs are counted by units only, not value -- the totals above are a floor, not an
              overstatement.
            </p>
          </>
        )}
      </Section>

      {/* Outlet risk overview */}
      <Section title="Outlet risk overview" note="Each column scaled to its own range, not a shared scale">
        {(stockAlerts.isLoading || variance.isLoading || stuckCounts.isLoading) && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {riskData.length > 0 && (
          <>
            <Heatmap
              rows={riskRows}
              columns={riskColumns}
              data={riskData}
              formatValue={(v, colId) => (colId === "oos_rate" || colId === "low_rate" ? `${v}%` : formatCurrency(v))}
              normalize="column"
            />
            <p className="mt-3 text-xs text-foreground/50">
              OOS/low-cover rates are a share of each outlet&rsquo;s own actively-selling positions, not raw counts --
              a bigger assortment doesn&rsquo;t automatically look worse. Count variance and stuck value are this
              month&rsquo;s totals, not rates.
            </p>
          </>
        )}
      </Section>

      {/* Availability & cover */}
      <Section title="Availability & cover" note="Actively-selling products only">
        {stockAlerts.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {stockAlerts.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load stock alerts.</p>}
        {sa && (
          <>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
              Top-selling products currently out of stock
            </h3>
            <p className="mt-1 text-xs text-foreground/50">
              Ranked by units sold in the 30 days before the stockout -- recent sales velocity, not a claim about
              demand actually lost during it.
            </p>
            <div className="mt-2">
              <RankedBarChart
                data={(sa.top_out_of_stock ?? []).slice(0, 10).map((i) => ({
                  name: `${stripSkuSuffix(i.name)} · ${outletShort(i.outlet)}`,
                  value: i.sold_30d,
                }))}
                formatValue={(v) => `${formatNumber(v)} sold/30d`}
                labelWidth={260}
                rowHeight={40}
              />
            </div>

            <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-foreground/50">Stock state by outlet</h3>
            <div className="mt-2">
              <Heatmap rows={stockStateRows} columns={stockStateColumns} data={stockStateData} formatValue={formatNumber} normalize="column" />
            </div>

            <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-foreground/50">
              Days-of-cover risk across the portfolio
            </h3>
            <p className="mt-1 text-xs text-foreground/50">Every actively-selling (outlet, product) position, not just the top 15 below.</p>
            <div className="mt-2">
              <OrderedBarList
                data={(sa.coverage_distribution ?? []).map((b) => ({ name: b.label, value: b.count }))}
                formatValue={formatNumber}
                labelWidth={110}
              />
            </div>
          </>
        )}

        <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-foreground/50">Top 15 sellers, trailing 30 days</h3>
        {stockCoverage.isLoading && <p className="mt-2 text-sm text-foreground/50">Loading&hellip;</p>}
        {stockCoverage.data?.status === "error" && <p className="mt-2 text-sm text-foreground/50">Couldn&rsquo;t load stock coverage.</p>}
        {stockCoverage.data?.status === "ok" && (
          <div className="mt-2">
            <StockCoverageTable data={stockCoverage.data} maxDays={coverageMaxDays} />
          </div>
        )}
        <p className="mt-3 text-xs text-foreground/50">
          Days of cover = current stock &divide; average daily sales. Flagged under{" "}
          {stockCoverage.data?.low_stock_threshold_days ?? 3} days (marked on each bar). &ldquo;Data issue&rdquo;
          means recorded stock is negative -- sold past what&rsquo;s on hand, kept separate from an ordinary
          low-cover reading.
        </p>
      </Section>

      {/* Stock count integrity */}
      <Section title="Stock count integrity">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Stuck -- started but never finished</h3>
        {stuckCounts.isLoading && <p className="mt-2 text-sm text-foreground/50">Loading&hellip;</p>}
        {stuckCounts.data?.status === "error" && <p className="mt-2 text-sm text-foreground/50">Couldn&rsquo;t load stuck stock counts.</p>}
        {stuck && stuck.sessions.length > 0 && (
          <div className="mt-2">
            <RankedBarChart
              data={stuck.sessions.map((s) => ({ name: `${s.name} · ${outletShort(s.outlet)}`, value: s.days_stuck }))}
              formatValue={(v) => `${v}d`}
              labelWidth={200}
              rowHeight={32}
            />
            <div className="mt-4">
              <StuckStockCountsTable sessions={stuck.sessions} />
            </div>
            <p className="mt-2 text-xs text-foreground/50">
              Any variance pulled off one of these is phantom -- un-scanned lines still carry the system&rsquo;s on-hand
              qty, not real shrinkage.
            </p>
          </div>
        )}

        <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-foreground/50">
          Completed -- real, physically-verified variance ({v?.period})
        </h3>
        {variance.isLoading && <p className="mt-2 text-sm text-foreground/50">Loading&hellip;</p>}
        {variance.data?.status === "error" && <p className="mt-2 text-sm text-foreground/50">Couldn&rsquo;t load completed variance.</p>}
        {v && v.session_count > 0 && (
          <div className="mt-2 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <div className="flex items-baseline justify-between text-sm">
                <span className="text-foreground/60">Net variance across {v.session_count} completed sessions</span>
                <span className="font-semibold text-foreground-strong">{formatCurrency(v.net_variance_value_rs)}</span>
              </div>
              <TrendChart
                data={varianceTrend}
                xKey="date"
                valueKey="value"
                formatX={(x) => formatDate(x)}
                formatValue={formatCurrency}
                height={200}
              />
              <p className="mt-1 text-xs text-foreground/50">Signed -- shows direction. Positive/negative sessions can offset in this total.</p>
            </div>
            {varianceByOutlet && (
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Absolute variance by outlet</h4>
                <div className="mt-2">
                  <RankedBarChart
                    data={varianceByOutlet.top_contributors.map((c) => ({ name: outletShort(c.name), value: c.current }))}
                    formatValue={formatCurrency}
                  />
                </div>
                <p className="mt-1 text-xs text-foreground/50">Sum of each session&rsquo;s absolute variance -- severity, not net direction.</p>
              </div>
            )}
          </div>
        )}
        {v && v.session_count === 0 && (
          <p className="mt-2 text-sm text-foreground/50">No completed stock counts for {v.period}.</p>
        )}
      </Section>

      {/* Loss & discrepancies */}
      <Section title="Loss & discrepancies" note={lad ? `Last complete month: ${lad.period}` : undefined}>
        {lossAndDamage.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {lossAndDamage.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load loss &amp; damage.</p>}
        {lad && (
          <>
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Shortage, by reason</h3>
                <div className="mt-2">
                  <RankedBarChart
                    data={(lad.shortage_by_reason ?? []).map((r) => ({ name: r.reason, value: r.qty }))}
                    formatValue={formatNumber}
                  />
                </div>
              </div>
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Reason &times; outlet</h3>
                <p className="mt-1 text-xs text-foreground/50">Is one outlet dominated by one reason, or an even mix?</p>
                <div className="mt-2">
                  <GroupedStackedBarChart data={reasonByOutletData} series={reasonSeries} formatValue={formatNumber} />
                </div>
              </div>
            </div>

            <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide text-foreground/50">Daily shortage qty, {lad.period}</h3>
            <div className="mt-2">
              <TrendChart data={shortageTrend} xKey="date" valueKey="value" formatX={(x) => formatDate(x)} formatValue={formatNumber} height={180} />
            </div>
            <p className="mt-1 text-xs text-foreground/50">Isolated spike, persistent, growing, or shrinking -- a monthly total alone can&rsquo;t say.</p>

            <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Top shortage products</h3>
                <div className="mt-2">
                  <RankedBarChart
                    data={(lad.shortage_top_products ?? []).slice(0, 8).map((p) => ({ name: stripSkuSuffix(p.name), value: p.qty }))}
                    formatValue={formatNumber}
                    labelWidth={200}
                    rowHeight={40}
                  />
                </div>
              </div>
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Top damaged products</h3>
                <div className="mt-2">
                  <RankedBarChart
                    data={(lad.damaged_top_products ?? []).slice(0, 8).map((p) => ({ name: stripSkuSuffix(p.name), value: p.qty }))}
                    formatValue={formatNumber}
                    labelWidth={200}
                    rowHeight={40}
                  />
                </div>
              </div>
            </div>
          </>
        )}
      </Section>

      {/* Tracked issues -- closes the loop to the case workflow, no charts */}
      <Section title="Tracked issues" note={cases.data?.status === "ok" ? `${formatNumber(cases.data.total)} open` : undefined}>
        {cases.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {cases.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load inventory cases.</p>}
        {cases.data?.status === "ok" && openCases.length === 0 && (
          <p className="text-sm text-foreground/50">No open inventory cases right now.</p>
        )}
        {openCases.length > 0 && (
          <div className="space-y-2">
            {openCases.map((item) => (
              <CaseRow key={item.case_id} item={item} />
            ))}
          </div>
        )}
        <Link to="/inventory/cases" className="mt-3 inline-block text-xs font-semibold text-accent">
          See all inventory cases &rarr;
        </Link>
      </Section>
    </div>
  );
}
