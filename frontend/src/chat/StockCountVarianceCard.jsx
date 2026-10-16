import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import HBarList from "../charts/HBarList";
import Button from "../design/Button";
import { formatCurrency, formatNumber } from "../format";
import { saveCompletedStockCountVarianceCase } from "../api";

/** Completed Stock-Count Variance & Shrinkage Investigation: renders
 * stock_counts.get_completed_stock_count_variance()'s raw result exactly
 * as returned -- no client-side evidence computation, that's what the
 * LLM's own reply narrates from the same data. Deliberately distinct from
 * StockCountInvestigationPanel.jsx (Investigate's case-detail view): this
 * is the on-demand chat card, this app's first interactive tool-call card
 * (every other one here is pure display), for exactly one action -- "Save
 * as case," turning this ad-hoc investigation into a persistent, trackable
 * case via case_engine.save_ad_hoc_case. */
export default function StockCountVarianceCard({ result }) {
  const [saveState, setSaveState] = useState("idle"); // idle | saving | saved | error
  const [savedCaseId, setSavedCaseId] = useState(null);

  if (!result || result.error) return null;

  const outletBreakdown = (result.breakdowns || []).find((b) => b.dimension === "store");
  const productTypeBreakdown = (result.breakdowns || []).find((b) => b.dimension === "product_type");

  async function handleSave() {
    setSaveState("saving");
    try {
      const saved = await saveCompletedStockCountVarianceCase(result.start_date, result.end_date);
      setSavedCaseId(saved.case_id);
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  }

  return (
    <div className="tool-card stock-count-variance-card">
      <div className="tool-card-title">Completed stock-count variance &mdash; {result.period}</div>
      <div className="tool-card-grid">
        <div>
          <span className="tool-card-label">Completed sessions</span>
          <span className="tool-card-value">{formatNumber(result.session_count)}</span>
        </div>
        <div>
          <span className="tool-card-label">Gross variance (absolute)</span>
          <span className="tool-card-value">{formatCurrency(result.gross_abs_variance_value_rs)}</span>
        </div>
        <div>
          <span className="tool-card-label">Net variance (signed)</span>
          <span className="tool-card-value">{formatCurrency(result.net_variance_value_rs)}</span>
        </div>
        <div>
          <span className="tool-card-label">Variance rate</span>
          <span className="tool-card-value">
            {result.variance_rate_pct != null ? `${result.variance_rate_pct}%` : "Unknown"}
          </span>
        </div>
      </div>

      {result.session_count === 0 ? (
        <div className="tool-card-notice">No completed stock counts recorded for this period.</div>
      ) : (
        <>
          <div className="customer-intel-row">
            {outletBreakdown && (
              <div className="chart-card">
                <div className="chart-card-title">Variance by outlet</div>
                <HBarList
                  data={outletBreakdown.top_contributors}
                  valueKey="current"
                  formatValue={formatCurrency}
                  ariaLabel="Completed stock-count variance by outlet"
                />
              </div>
            )}
            {productTypeBreakdown && (
              <div className="chart-card">
                <div className="chart-card-title">Variance by product type</div>
                <HBarList
                  data={productTypeBreakdown.top_contributors}
                  valueKey="current"
                  formatValue={formatCurrency}
                  ariaLabel="Completed stock-count variance by product type"
                />
              </div>
            )}
          </div>

          <div className="tool-card-notice">
            Gross variance sums every line's absolute discrepancy; net variance nets over- and under-counts
            against each other. Neither figure is confirmed inventory loss on its own.
          </div>

          <div className="mt-2">
            {saveState === "saved" ? (
              <span className="tool-card-notice">
                Saved as case {savedCaseId} —{" "}
                <Link to={`/investigate/${encodeURIComponent(savedCaseId)}`} className="tool-card-footer-link">
                  Open in Investigate <ArrowRight size={12} className="inline" />
                </Link>
              </span>
            ) : (
              <Button size="sm" onClick={handleSave} disabled={saveState === "saving"}>
                {saveState === "saving" ? "Saving…" : "Save as case"}
              </Button>
            )}
            {saveState === "error" && (
              <span className="tool-card-notice"> Couldn't save this as a case — try again.</span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
