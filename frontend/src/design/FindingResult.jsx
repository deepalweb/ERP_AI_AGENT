import TrendChart from "../charts/TrendChart";
import { formatByUnit, formatCurrency, formatConfirmPeriod } from "../format";
import CompletedVarianceInvestigationPanel from "./CompletedVarianceInvestigationPanel";
import ControlsInvestigationPanel from "./ControlsInvestigationPanel";
import ExtremeEventInvestigationPanel from "./ExtremeEventInvestigationPanel";
import IncidentInvestigationPanel from "./IncidentInvestigationPanel";
import InventoryInvestigationPanel from "./InventoryInvestigationPanel";
import RevenueInvestigationPanel from "./RevenueInvestigationPanel";
import SecondOrderConversionInvestigationPanel from "./SecondOrderConversionInvestigationPanel";
import StockCountInvestigationPanel from "./StockCountInvestigationPanel";
import SupplierInvestigationPanel from "./SupplierInvestigationPanel";
import TransferDiscrepancyInvestigationPanel from "./TransferDiscrepancyInvestigationPanel";
import OperationFlowInvestigationPanel from "./OperationFlowInvestigationPanel";
import StockReconciliationInvestigationPanel from "./StockReconciliationInvestigationPanel";
import FreshProductionInvestigationPanel from "./FreshProductionInvestigationPanel";
import PriceIntelligenceInvestigationPanel from "./PriceIntelligenceInvestigationPanel";

/** Metric/domain evidence modules plugged into the shared case-detail shell.
 * The renderer map is deliberately explicit; unknown result keys use a safe
 * no-op instead of inheriting another finding's specialized presentation. */
const RESULT_RENDERERS = {
  stuck_stock_count: ({ latest, trendPoints, onRetry }) => <StockCountInvestigationPanel latest={latest} trendPoints={trendPoints} onRetry={onRetry} />,
  inventory_stock: ({ latest }) => <InventoryInvestigationPanel latest={latest} asOfDate={latest?.confirm?.period} />,
  revenue: ({ latest }) => <RevenueInvestigationPanel latest={latest} casePeriod={latest?.confirm?.period} />,
  completed_variance: ({ latest }) => <CompletedVarianceInvestigationPanel latest={latest} />,
  incident: ({ latest }) => <IncidentInvestigationPanel latest={latest} />,
  transfer_discrepancy: ({ latest }) => <TransferDiscrepancyInvestigationPanel latest={latest} />,
  extreme_event: ({ latest }) => <ExtremeEventInvestigationPanel latest={latest} />,
  controls: ({ latest, statusLabel }) => <ControlsInvestigationPanel latest={latest} statusLabel={statusLabel} />,
  supplier: ({ latest }) => <SupplierInvestigationPanel latest={latest} />,
  second_order_conversion: ({ latest }) => <SecondOrderConversionInvestigationPanel latest={latest} />,
  operation_flow: ({ latest }) => <OperationFlowInvestigationPanel latest={latest} />,
  stock_reconciliation: ({ latest }) => <StockReconciliationInvestigationPanel latest={latest} />,
  fresh_production: ({ latest }) => <FreshProductionInvestigationPanel latest={latest} />,
  price_intelligence: ({ latest }) => <PriceIntelligenceInvestigationPanel latest={latest} />,
  trend: ({ trendPoints, latest }) => <div>
    <p className="mb-2 text-xs text-foreground-subtle">Recorded observations over time (real scan history, never interpolated).</p>
    <TrendChart data={trendPoints} formatValue={(value) => formatByUnit(value, latest?.confirm?.unit)} stroke="var(--accent)" gridStroke="var(--border)" axisStroke="var(--text-subtle)" />
  </div>,
  opportunity: ({ latest }) => {
    const confirm = latest?.confirm;
    const amount = latest?.impact?.financial_impact_rs;
    return <div className="space-y-3 text-sm">
      <p className="text-foreground-strong">{latest?.title ?? "Opportunity signal"}</p>
      {confirm && <p className="text-foreground-subtle">
        {confirm.baseline == null ? "Before value unavailable" : `Before: ${formatByUnit(confirm.baseline, confirm.unit)}`}
        {" · "}{confirm.current == null ? "After value unavailable" : `After: ${formatByUnit(confirm.current, confirm.unit)}`}
        {confirm.period ? ` · ${formatConfirmPeriod(confirm.period)}` : ""}
      </p>}
      {amount != null && <p className="text-foreground-subtle">Estimated benefit: {formatCurrency(amount)}. This is not verified realized savings.</p>}
      {latest?.impact?.detail && <p className="text-foreground-subtle">{latest.impact.detail}</p>}
      <p className="text-xs text-foreground-subtle">This signal identifies a favorable outcome; it does not establish what caused it.</p>
    </div>;
  },
};

export default function FindingResult({ result, ...props }) {
  const render = RESULT_RENDERERS[result];
  return render ? render(props) : null;
}
