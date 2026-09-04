import { useEffect, useState } from "react";
import "./shared.css";
import "./Overview.css";
import { fetchCustomerIntelligence, fetchForwardLook, fetchInventoryDepth } from "./api";
import StatTile from "./charts/StatTile";
import LineChart from "./charts/LineChart";
import BarChart from "./charts/BarChart";
import HBarList from "./charts/HBarList";
import AnomaliesTable from "./charts/AnomaliesTable";
import HealthScore from "./charts/HealthScore";
import AIInsightPanel from "./AIInsightPanel";
import AIImpact from "./AIImpact";
import CustomerIntelligence from "./CustomerIntelligence";
import InventoryDepth from "./InventoryDepth";
import ForwardLook from "./ForwardLook";
import ActionCenter from "./ActionCenter";
import SignalStrip from "./SignalStrip";
import { formatCurrency, formatNumber, monthLabel } from "./format";

export default function Overview({ investigations, tracking, onOpenInvestigation, dashboardData: data, health }) {
  const [customerIntel, setCustomerIntel] = useState(null);
  const [inventoryDepth, setInventoryDepth] = useState(null);
  const [forwardLook, setForwardLook] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([fetchCustomerIntelligence(), fetchInventoryDepth(), fetchForwardLook()])
      .then(([customerRes, inventoryRes, forwardLookRes]) => {
        if (cancelled) return;
        if (customerRes.status !== "error") {
          setCustomerIntel(customerRes);
        }
        if (inventoryRes.status !== "error") {
          setInventoryDepth(inventoryRes);
        }
        if (forwardLookRes.status !== "error") {
          setForwardLook(forwardLookRes);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not reach the backend. Is it running on port 8000?");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (!data || loading) {
    return <div className="page-state">Loading overview&hellip; running real analysis, give it a moment.</div>;
  }

  if (error) {
    return <div className="page-state page-state-error">{error}</div>;
  }

  const { sales, top_products: topProducts, top_customers: topCustomers, inventory } = data;
  const avgOrderValue = sales.headline_orders ? sales.headline_revenue / sales.headline_orders : 0;
  const lastMonth = sales.months[sales.months.length - 1];
  const revenueTrend = sales.months.map((m) => m.revenue);
  const ordersTrend = sales.months.map((m) => m.sale_orders);
  const topInvestigation = investigations?.[0] ?? null;
  const attentionCount = investigations?.filter((inv) => inv.category === "critical").length ?? 0;
  const opportunityCount = investigations?.filter((inv) => inv.category === "opportunity").length ?? 0;

  return (
    <div className="overview">
      <section className="dashboard-heading">
        <div>
          <h2>Executive overview</h2>
          <p>{attentionCount} critical &middot; {opportunityCount} opportunities</p>
        </div>
        <span className="briefing-date">Data through {lastMonth ? monthLabel(lastMonth.month) : "n/a"}</span>
      </section>

      <div className="dashboard-top-grid">
        <HealthScore health={health} />
        <AIInsightPanel investigation={topInvestigation} onOpenInvestigation={onOpenInvestigation} />
      </div>

      <SignalStrip health={health} />

      <section className="business-pulse" aria-labelledby="business-pulse-title">
        <div className="section-heading-row">
          <h2 id="business-pulse-title">Business performance</h2>
          <span className="overview-subtitle">{formatNumber(sales.total_orders)} orders analyzed</span>
        </div>
        <div className="kpi-row">
        <StatTile
          label={`Revenue (${sales.headline_month ? monthLabel(sales.headline_month) : "last month"})`}
          value={formatCurrency(sales.headline_revenue)}
          delta={sales.revenue_growth_pct}
          trend={revenueTrend}
        />
        <StatTile
          label={`Orders (${sales.headline_month ? monthLabel(sales.headline_month) : "last month"})`}
          value={formatNumber(sales.headline_orders)}
          delta={sales.orders_growth_pct}
          trend={ordersTrend}
        />
        <StatTile label="Avg. order value" value={formatCurrency(avgOrderValue)} />
        <StatTile
          label="Cancellation rate"
          value={`${sales.overall_cancellation_rate_pct}%`}
          status={sales.overall_cancellation_rate_pct > 10 ? "critical" : undefined}
          note={`${formatNumber(sales.total_cancelled)} cancelled orders`}
        />
        </div>
      </section>

      <section className="visual-dashboard-grid">
          <div className="chart-card chart-card-wide overview-hero-chart">
            <div className="chart-card-title">Revenue trend <span>{sales.revenue_growth_pct > 0 ? "+" : ""}{sales.revenue_growth_pct}%</span></div>
            <LineChart data={sales.months} valueKey="revenue" ariaLabel="Monthly revenue trend line chart" />
          </div>
        <div className="chart-card">
          <div className="chart-card-title">Order volume <span>{sales.orders_growth_pct > 0 ? "+" : ""}{sales.orders_growth_pct}%</span></div>
          <BarChart data={sales.months} valueKey="sale_orders" formatValue={formatNumber} ariaLabel="Monthly confirmed order volume" />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Product leaders</div>
          <HBarList
            data={topProducts.slice(0, 5)}
            valueKey="revenue"
            detailKey="qty"
            detailLabel="units sold"
            ariaLabel="Top products by revenue"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Customer leaders</div>
          <HBarList
            data={topCustomers.slice(0, 5)}
            valueKey="revenue"
            detailKey="orders"
            detailLabel="orders"
            ariaLabel="Top customers by revenue"
          />
        </div>
        <div className="chart-card">
          <div className="chart-card-title">Inventory risk</div>
          <div className="chart-card-tiles">
            <StatTile
              label="Low stock"
              value={formatNumber(inventory.low_stock_count)}
              status="warning"
              note={`≤ ${inventory.low_stock_threshold} units`}
            />
            <StatTile label="Out of stock" value={formatNumber(inventory.out_of_stock_count)} status="critical" />
          </div>
          <BarChart data={inventory.po_spend_by_month} valueKey="spend" formatValue={formatCurrency} ariaLabel="Monthly purchase order spend" />
        </div>
      </section>

      <section className="attention-section" aria-labelledby="attention-section-title">
        <div className="section-heading-row">
          <div>
            <div className="section-kicker">Areas needing attention</div>
            <h2 id="attention-section-title">What to look into</h2>
          </div>
        </div>
        <ActionCenter investigations={investigations} onOpenInvestigation={onOpenInvestigation} />
      </section>

      <details className="evidence-disclosure">
        <summary>Customer, inventory and anomaly details</summary>
        <div className="evidence-content">
          <CustomerIntelligence data={customerIntel} />
          <InventoryDepth data={inventoryDepth} />
          <div className="chart-card overview-anomalies-card">
            <div className="chart-card-title">Changes detected by the intelligence engine</div>
            <AnomaliesTable investigations={investigations} onOpenInvestigation={onOpenInvestigation} />
          </div>
        </div>
      </details>

      <ForwardLook data={forwardLook} />
      <AIImpact tracking={tracking} />
    </div>
  );
}
