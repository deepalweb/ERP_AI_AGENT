import { formatNumber } from "../format";

function minutes(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} min` : "Not recorded";
}

export default function OperationFlowInvestigationPanel({ latest }) {
  const rows = latest?.operation_flow_rows ?? [];
  const isQuality = latest?.metric_id === "operation_flow_stage_data_quality";

  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-semibold text-foreground-strong">{latest?.title ?? "Order fulfillment flow"}</p>
        <p className="mt-1 text-foreground-subtle">{latest?.impact?.detail ?? "No detail is available."}</p>
      </div>
      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[620px] text-left text-xs">
            <thead className="bg-surface-sunken text-foreground-subtle">
              <tr>
                {(isQuality
                  ? ["Check", "Affected orders"]
                  : ["Celeste outlet", "Stage", "Recent p90", "Prior p95", "Alert threshold", "Slow days", "Recent orders"]
                ).map((heading) => <th key={heading} className="px-3 py-2 font-medium">{heading}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.outlet ?? row.issue}-${row.stage ?? ""}-${index}`} className="border-t border-border text-foreground">
                  {isQuality ? <>
                    <td className="px-3 py-2">{row.issue}</td>
                    <td className="px-3 py-2 tabular-nums">{formatNumber(row.count)}</td>
                  </> : <>
                    <td className="px-3 py-2">{row.outlet}</td>
                    <td className="px-3 py-2">{row.stage}</td>
                    <td className="px-3 py-2 tabular-nums">{minutes(row.recent_p90_minutes)}</td>
                    <td className="px-3 py-2 tabular-nums">{minutes(row.baseline_p95_minutes)}</td>
                    <td className="px-3 py-2 tabular-nums">{minutes(row.threshold_minutes)}</td>
                    <td className="px-3 py-2 tabular-nums">{row.slow_days} / 7</td>
                    <td className="px-3 py-2 tabular-nums">{formatNumber(row.recent_orders)}</td>
                  </>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-foreground-subtle">
        These records show process timing or timestamp quality. They do not identify picker/packer headcount or establish an individual cause. Source freshness remains unconfirmed because the reporting feed has no refresh watermark.
      </p>
    </div>
  );
}
