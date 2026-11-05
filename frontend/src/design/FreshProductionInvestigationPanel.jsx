import { formatNumber } from "../format";

function MissedTable({ rows }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[820px] text-left text-xs">
        <thead className="bg-surface-sunken text-foreground-subtle">
          <tr>
            {["Document", "Batch", "Product", "Round", "Scheduled for", "Units planned", "Responsible"].map((heading) => (
              <th key={heading} className="px-3 py-2 font-medium">{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.id}-${index}`} className="border-t border-border text-foreground">
              <td className="px-3 py-2 font-mono">{row.document ?? "—"}</td>
              <td className="px-3 py-2 font-mono text-foreground-subtle">{row.batch_reference ?? "—"}</td>
              <td className="px-3 py-2">{row.product ?? "—"}</td>
              <td className="px-3 py-2 capitalize">{row.round ?? "—"}</td>
              <td className="px-3 py-2">{row.scheduled_at ?? row.date ?? "—"}</td>
              <td className="px-3 py-2 tabular-nums">{formatNumber(row.qty)}</td>
              <td className="px-3 py-2">{row.responsible ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShortfallTable({ rows }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[960px] text-left text-xs">
        <thead className="bg-surface-sunken text-foreground-subtle">
          <tr>
            {["Document", "Batch", "Product", "Round", "Scheduled for", "Completed", "Planned", "Produced", "Deviation", "Responsible", "Completed by"].map((heading) => (
              <th key={heading} className="px-3 py-2 font-medium">{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.id}-${index}`} className="border-t border-border text-foreground">
              <td className="px-3 py-2 font-mono">{row.document ?? "—"}</td>
              <td className="px-3 py-2 font-mono text-foreground-subtle">{row.batch_reference ?? "—"}</td>
              <td className="px-3 py-2">{row.product ?? "—"}</td>
              <td className="px-3 py-2 capitalize">{row.round ?? "—"}</td>
              <td className="px-3 py-2">{row.scheduled_at ?? row.date ?? "—"}</td>
              <td className="px-3 py-2">{row.completed_at ?? "—"}</td>
              <td className="px-3 py-2 tabular-nums">{formatNumber(row.planned_qty)}</td>
              <td className="px-3 py-2 tabular-nums">{formatNumber(row.produced_qty)}</td>
              <td className={`px-3 py-2 text-right tabular-nums font-medium ${row.shortfall_qty > 0 ? "text-critical" : "text-good"}`}>
                {row.shortfall_qty > 0 ? "−" : "+"}{formatNumber(Math.abs(row.shortfall_qty))} ({row.pct_dev}%)
              </td>
              <td className="px-3 py-2">{row.responsible ?? "—"}</td>
              <td className="px-3 py-2">{row.completed_by ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CycleTimeTable({ rows }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[920px] text-left text-xs">
        <thead className="bg-surface-sunken text-foreground-subtle">
          <tr>
            {["Document", "Batch", "Product", "Round", "Scheduled for", "Completed", "Cycle time", "Round's own cutoff", "Responsible", "Completed by"].map((heading) => (
              <th key={heading} className="px-3 py-2 font-medium">{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.id}-${index}`} className="border-t border-border text-foreground">
              <td className="px-3 py-2 font-mono">{row.document ?? "—"}</td>
              <td className="px-3 py-2 font-mono text-foreground-subtle">{row.batch_reference ?? "—"}</td>
              <td className="px-3 py-2">{row.product ?? "—"}</td>
              <td className="px-3 py-2 capitalize">{row.round ?? "—"}</td>
              <td className="px-3 py-2">{row.scheduled_at ?? row.date ?? "—"}</td>
              <td className="px-3 py-2">{row.completed_at ?? "—"}</td>
              <td className="px-3 py-2 text-right tabular-nums font-medium text-critical">{formatNumber(row.cycle_hours)}h</td>
              <td className="px-3 py-2 tabular-nums text-foreground-subtle">{formatNumber(row.round_cutoff_hours)}h</td>
              <td className="px-3 py-2">{row.responsible ?? "—"}</td>
              <td className="px-3 py-2">{row.completed_by ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CAPTIONS = {
  missed:
    "Each row is a fresh-round manufacturing order scheduled at the central warehouse that was never " +
    "confirmed into actual production — this does not establish why (staffing, over-proposed rounds, " +
    "or a process gap), only that the product was never made for that round. \"Responsible\" is the " +
    "order's assigned user, not necessarily who should have confirmed it. There is no separate transfer " +
    "or delivery document for this process — the manufacturing order itself and its batch reference " +
    "(the daily fresh-request group it belongs to) are the only paper trail.",
  shortfall:
    "Each row is a completed fresh-round production whose output quantity differs meaningfully from " +
    "plan — most completed rounds match exactly, so this is the real tail, not rounding. This does not " +
    "establish whether the cause is raw-material availability, repack yield loss, or an unrealistic plan. " +
    "\"Completed by\" is who last updated the order, which is usually who closed it out.",
  cycle_time:
    "Each row is a completed fresh-round production that took longer than the slowest 5% of its own round " +
    "type (morning and evening run on genuinely different tempos, so each is judged against its own history, " +
    "not a shared cutoff). There is no separate \"demand submitted\" event — this round's scheduled start is " +
    "already within seconds of when the demand was created, so cycle time here is effectively demand-to-ready " +
    "lead time. This does not establish whether the cause is raw-material availability, staffing, or a backlog " +
    "from missed rounds spilling into the ones that do get made.",
};

export default function FreshProductionInvestigationPanel({ latest }) {
  const missedRows = latest?.fresh_production_missed_rows;
  const shortfallRows = latest?.fresh_production_shortfall_rows;
  const cycleTimeRows = latest?.fresh_production_cycle_time_rows;
  const mode = Array.isArray(missedRows) ? "missed" : Array.isArray(cycleTimeRows) ? "cycle_time" : "shortfall";
  const rows = missedRows ?? cycleTimeRows ?? shortfallRows ?? [];

  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-semibold text-foreground-strong">{latest?.title ?? "Fresh production"}</p>
        <p className="mt-1 text-foreground-subtle">{latest?.impact?.detail ?? "No detail is available."}</p>
      </div>
      {rows.length > 0 && (
        mode === "missed" ? <MissedTable rows={rows} />
        : mode === "cycle_time" ? <CycleTimeTable rows={rows} />
        : <ShortfallTable rows={rows} />
      )}
      <p className="text-xs text-foreground-subtle">{CAPTIONS[mode]}</p>
    </div>
  );
}
