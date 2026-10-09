import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  fetchInventoryDepth,
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
import DonutChart from "../charts/DonutChart";
import RadialGauge from "../charts/RadialGauge";
import RankedBarChart from "../charts/RankedBarChart";
import TrendChart from "../charts/TrendChart";
import StatTile from "../charts/StatTile";
import { formatCurrency, formatDate, formatNumber, stripSkuSuffix } from "../format";

const DEPTH_COLOR = { healthy: "var(--status-good)", overstock: "var(--status-warning)", dead_stock: "var(--status-critical)" };

function daysStuckTone(days) {
  return days >= 30 ? "critical" : days >= 7 ? "warning" : "good";
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

function StockCoverageTable({ data }) {
  if (!data || !data.products || data.products.length === 0) {
    return <p className="text-sm text-foreground/50">No stock coverage data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Product</th>
            <th className="pb-2 text-right font-medium">Sold (30d)</th>
            <th className="pb-2 text-right font-medium">Current stock</th>
            <th className="pb-2 pr-4 text-right font-medium">Days of cover</th>
            <th className="pb-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {data.products.map((p) => (
            <tr key={p.sku ?? p.name} className="border-t border-border">
              <td className="py-1.5">
                <div className="font-medium text-foreground-strong">{stripSkuSuffix(p.name)}</div>
                {p.sku && <div className="text-xs text-foreground/50">SKU {p.sku}</div>}
              </td>
              <td className="py-1.5 text-right">{formatNumber(p.qty_sold_30d)}</td>
              <td className="py-1.5 text-right">{formatNumber(p.current_stock)}</td>
              <td className="py-1.5 pr-4 text-right">{p.days_of_cover ?? "n/a"}</td>
              <td className="py-1.5">
                {p.negative_stock ? (
                  <Pill tone="critical">Data issue</Pill>
                ) : p.short ? (
                  <Pill tone="warning">{`Under ${data.low_stock_threshold_days}d cover`}</Pill>
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

function StockAlertsByOutletTable({ segments }) {
  if (!segments || segments.length === 0) {
    return <p className="text-sm text-foreground/50">No stock alert data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-foreground/60">
            <th className="pb-2 font-medium">Outlet</th>
            <th className="pb-2 text-right font-medium">Out of stock</th>
            <th className="pb-2 text-right font-medium">Low stock</th>
          </tr>
        </thead>
        <tbody>
          {segments.map((o) => (
            <tr key={o.outlet} className="border-t border-border">
              <td className="py-1.5 font-medium text-foreground-strong">{o.outlet}</td>
              <td className="py-1.5 text-right">
                {o.out_of_stock > 0 ? <Pill tone="critical">{o.out_of_stock}</Pill> : formatNumber(0)}
              </td>
              <td className="py-1.5 text-right">
                {o.low_stock > 0 ? <Pill tone="warning">{o.low_stock}</Pill> : formatNumber(0)}
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
  const sa = stockAlerts.data?.status === "ok" ? stockAlerts.data : null;
  const stuck = stuckCounts.data?.status === "ok" ? stuckCounts.data : null;
  const v = variance.data?.status === "ok" ? variance.data : null;
  const lad = lossAndDamage.data?.status === "ok" ? lossAndDamage.data : null;
  const openCases = cases.data?.status === "ok" ? cases.data.items : [];

  const segByid = Object.fromEntries((d?.segments ?? []).map((s) => [s.id, s]));
  const totalDepthValue = (d?.segments ?? []).reduce((sum, s) => sum + s.value, 0);
  const healthyPct = totalDepthValue ? ((segByid.healthy?.value ?? 0) / totalDepthValue) * 100 : 0;
  const healthStatus = healthyPct >= 70 ? "good" : healthyPct >= 50 ? "warning" : "critical";

  const shortageQty = lad?.shortage_by_reason?.reduce((sum, r) => sum + r.qty, 0) ?? null;
  const varianceByOutlet = v?.breakdowns?.find((b) => b.dimension === "store");

  // Grouped by day (several sessions can complete the same day) so the
  // trend line shows one point per day, not several stacked at the same x.
  const varianceTrend = v?.sessions
    ? Object.entries(
        v.sessions.reduce((byDay, s) => {
          const day = s.completed_at.slice(0, 10);
          byDay[day] = (byDay[day] ?? 0) + s.net_variance_value_rs;
          return byDay;
        }, {})
      )
        .map(([date, value]) => ({ date, value }))
        .sort((a, b) => (a.date < b.date ? -1 : 1))
    : [];

  return (
    <div className="px-6 py-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground-strong">Inventory intelligence</h1>
          <p className="mt-1 text-sm text-foreground/60">
            Stock health, what's actually costing us sales, count integrity, and loss &amp; damage -- in one place.
          </p>
        </div>
        {d?.as_of_date && <span className="text-xs text-foreground/60">Data through {formatDate(d.as_of_date)}</span>}
      </div>

      {/* KPI strip */}
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile
          label="Out of stock"
          value={sa ? formatNumber(sa.distinct_out_of_stock_products) : "--"}
          note="distinct products"
          status="critical"
        />
        <StatTile
          label="Low stock"
          value={sa ? formatNumber(sa.distinct_low_stock_products) : "--"}
          note="distinct products"
          status="warning"
        />
        <StatTile
          label="Dead stock"
          value={segByid.dead_stock ? formatCurrency(segByid.dead_stock.value) : "--"}
          note={segByid.dead_stock ? `${formatNumber(segByid.dead_stock.count)} SKUs` : undefined}
          status="critical"
        />
        <StatTile
          label="Overstock"
          value={segByid.overstock ? formatCurrency(segByid.overstock.value) : "--"}
          note={segByid.overstock ? `${formatNumber(segByid.overstock.count)} SKUs` : undefined}
          status="warning"
        />
        <StatTile
          label="Stuck stock counts"
          value={stuck ? formatNumber(stuck.sessions.length) : "--"}
          note={stuck ? `${formatCurrency(stuck.total_value_at_risk_rs)} at risk` : undefined}
          status="warning"
        />
        <StatTile
          label="Shortage qty (last month)"
          value={shortageQty != null ? formatNumber(shortageQty) : "--"}
          note={lad?.period}
          status="critical"
        />
      </div>

      {/* Stock health */}
      <Section title="Stock health" note={d ? `${formatNumber(d.segments.reduce((n, s) => n + s.count, 0))} SKUs tracked` : undefined}>
        {depth.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {depth.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load inventory depth.</p>}
        {d && (
          <>
            <div className="flex flex-wrap items-center gap-8">
              <RadialGauge value={healthyPct} status={healthStatus} sublabel="healthy by value" size={120} />
              <div className="w-64 flex-shrink-0">
                <DonutChart
                  data={[
                    { name: "Healthy", value: segByid.healthy?.value ?? 0 },
                    { name: "Overstock", value: segByid.overstock?.value ?? 0 },
                    { name: "Dead stock", value: segByid.dead_stock?.value ?? 0 },
                  ]}
                  colors={[DEPTH_COLOR.healthy, DEPTH_COLOR.overstock, DEPTH_COLOR.dead_stock]}
                  formatValue={formatCurrency}
                />
              </div>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Top dead stock, by value</h3>
                <div className="mt-2">
                  <RankedBarChart
                    data={(d.dead_stock_items ?? []).slice(0, 8).map((i) => ({ name: stripSkuSuffix(i.name), value: i.estimated_value }))}
                    formatValue={formatCurrency}
                    labelWidth={200}
                    rowHeight={40}
                  />
                </div>
              </div>
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Top overstock, by value</h3>
                <div className="mt-2">
                  <RankedBarChart
                    data={(d.overstock_items ?? []).slice(0, 8).map((i) => ({ name: stripSkuSuffix(i.name), value: i.estimated_value }))}
                    formatValue={formatCurrency}
                    labelWidth={200}
                    rowHeight={40}
                  />
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

      {/* Stock alerts -- what's actually costing us sales */}
      <Section title="Stock alerts" note="Actively-selling products only">
        {stockAlerts.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {stockAlerts.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load stock alerts.</p>}
        {sa && (
          <>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">
              Top out-of-stock, by recent sales lost
            </h3>
            <div className="mt-2">
              <RankedBarChart
                data={(sa.top_out_of_stock ?? []).slice(0, 10).map((i) => ({
                  name: `${stripSkuSuffix(i.name)} · ${i.outlet.replace("Celeste Daily", "").replace("-", "").trim()}`,
                  value: i.sold_30d,
                }))}
                formatValue={(v) => `${formatNumber(v)} sold/30d`}
                labelWidth={260}
                rowHeight={40}
              />
            </div>
            <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-foreground/50">By outlet</h3>
            <div className="mt-2">
              <StockAlertsByOutletTable segments={sa.segments_by_outlet} />
            </div>
          </>
        )}
      </Section>

      {/* Stock coverage -- will we run out soon */}
      <Section title="Stock coverage" note="Top 15 sellers by units sold, trailing 30 days">
        {stockCoverage.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {stockCoverage.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load stock coverage.</p>}
        {stockCoverage.data?.status === "ok" && <StockCoverageTable data={stockCoverage.data} />}
        <p className="mt-3 text-xs text-foreground/50">
          Days of cover = current stock &divide; average daily sales. Flagged when under{" "}
          {stockCoverage.data?.low_stock_threshold_days ?? 3} days. &ldquo;Data issue&rdquo; means recorded stock is
          negative -- sold past what&rsquo;s on hand.
        </p>
      </Section>

      {/* Stock count integrity */}
      <Section title="Stock count integrity">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Stuck -- started but never finished</h3>
        {stuckCounts.isLoading && <p className="mt-2 text-sm text-foreground/50">Loading&hellip;</p>}
        {stuckCounts.data?.status === "error" && <p className="mt-2 text-sm text-foreground/50">Couldn&rsquo;t load stuck stock counts.</p>}
        {stuck && (
          <div className="mt-2">
            <StuckStockCountsTable sessions={stuck.sessions} />
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
            </div>
            {varianceByOutlet && (
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">{varianceByOutlet.label}</h4>
                <div className="mt-2">
                  <RankedBarChart
                    data={varianceByOutlet.top_contributors.map((c) => ({ name: c.name.replace("Celeste Daily", "").replace("-", "").trim(), value: c.current }))}
                    formatValue={formatCurrency}
                  />
                </div>
              </div>
            )}
          </div>
        )}
        {v && v.session_count === 0 && (
          <p className="mt-2 text-sm text-foreground/50">No completed stock counts for {v.period}.</p>
        )}
      </Section>

      {/* Loss and damage */}
      <Section title="Loss & damage" note={lad ? `Last complete month: ${lad.period}` : undefined}>
        {lossAndDamage.isLoading && <p className="text-sm text-foreground/50">Loading&hellip;</p>}
        {lossAndDamage.data?.status === "error" && <p className="text-sm text-foreground/50">Couldn&rsquo;t load loss &amp; damage.</p>}
        {lad && (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Shortage, by reason</h3>
              <DonutChart
                data={(lad.shortage_by_reason ?? []).map((r) => ({ name: r.reason, value: r.qty }))}
                formatValue={formatNumber}
              />
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground/50">Shortage, by outlet</h3>
              <div className="mt-2">
                <RankedBarChart
                  data={(lad.shortage_by_store ?? []).map((s) => ({ name: s.outlet.replace("Celeste Daily", "").replace("-", "").trim(), value: s.qty }))}
                  formatValue={formatNumber}
                />
              </div>
            </div>
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
        )}
      </Section>

      {/* Tracked inventory issues -- closes the loop to the case workflow */}
      <Section title="Tracked inventory issues" note={cases.data?.status === "ok" ? `${formatNumber(cases.data.total)} open` : undefined}>
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
