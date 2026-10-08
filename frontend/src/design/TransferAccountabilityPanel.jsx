import { Area, AreaChart, Bar, BarChart, Cell, CartesianGrid, LabelList, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { formatNumber } from "../format";
import { displayPerson } from "./TransferFilterBar";

/** Where transfer units go missing, by the person who handled each step, by product, and over time.
 * Each person is one bubble: across = units handled, up = their missing rate on that volume,
 * size = units missing. A high rate points at a step to check, not a finding of fault.
 * The data is company-wide for the same window as the transfer findings, so it is context
 * for the finding, not part of its figures. Filters narrow this panel only. */

const MOVED = "var(--series-1)";
const RECURRING = "var(--status-warning)";
const ONE_OFF = "var(--text-subtle)";
const GRID = "var(--line, rgba(127,127,127,0.2))";

const shortProduct = (name) => (name ?? "").split(" - ")[0];
// Column labels: drop size and pack detail in brackets so names fit under a column.
const columnLabel = (name) => shortProduct(name).replace(/\s*\(\d[^)]*\)/g, "").trim();
const shortDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

function Legend({ items }) {
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-foreground-subtle">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function PersonTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs shadow">
      <div className="font-semibold text-foreground-strong">{row.label}</div>
      <div className="text-foreground">{formatNumber(row.missing_units)} missing units</div>
      <div className="text-foreground-subtle">Rate {row.missing_rate_pct}% of {formatNumber(row.expected_units)} handled</div>
      <div className="text-foreground-subtle">Largest single line {formatNumber(row.largest_line_missing)}</div>
    </div>
  );
}

function PersonScatter({ title, role, rows }) {
  const data = rows
    .filter((r) => r.person !== "Not recorded" && r.expected_units > 0)
    .map((r) => ({ ...r, label: displayPerson(r.person) }));
  return (
    <div className="min-w-0">
      <h4 className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">{title}</h4>
      <p className="mt-1 text-xs text-foreground-subtle">{role}</p>
      {data.length === 0 ? (
        <p className="mt-6 text-sm text-foreground-subtle">No handled volume for this step under these filters.</p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <ScatterChart margin={{ top: 28, right: 24, bottom: 24, left: 8 }}>
            <CartesianGrid stroke={GRID} />
            <XAxis type="number" dataKey="expected_units" name="Units handled" stroke="var(--chart-ink-muted)" fontSize={11} tickFormatter={(v) => formatNumber(v)} label={{ value: "Units handled", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--chart-ink-muted)" }} />
            <YAxis type="number" dataKey="missing_rate_pct" name="Missing rate" domain={[0, (max) => Math.max(max * 1.25, 0.5)]} tickFormatter={(v) => `${Number(v).toFixed(1)}%`} stroke="var(--chart-ink-muted)" fontSize={11} width={44} />
            <ZAxis type="number" dataKey="missing_units" range={[30, 380]} name="Missing units" />
            <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<PersonTooltip />} />
            <Scatter data={data} fill={MOVED} fillOpacity={0.75} isAnimationActive={false}>
              <LabelList dataKey="label" position="top" fontSize={11} fill="var(--text)" />
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function ProductTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs shadow">
      <div className="font-semibold text-foreground-strong">{row.product}</div>
      <div className="text-foreground">{formatNumber(row.missing_units)} of {formatNumber(row.expected_units)} units missing ({row.missing_rate_pct}%)</div>
      <div className="text-foreground-subtle">{formatNumber(row.short_moves)} short receipts across {row.outlets_short} outlet{row.outlets_short === 1 ? "" : "s"}</div>
      <div className="text-foreground-subtle">{row.pattern === "recurring" ? "Recurring" : "One-off"} · {row.share_of_missing_pct}% of the missing units shown</div>
    </div>
  );
}

function ProductBars({ rows }) {
  const data = [...rows]
    .sort((a, b) => b.missing_units - a.missing_units)
    .slice(0, 8)
    .map((r) => ({ ...r, label: columnLabel(r.product) }));
  return (
    <div>
      <Legend items={[
        { label: "Recurring: short on 3+ receipts across 2+ outlets", color: RECURRING },
        { label: "One-off", color: ONE_OFF },
      ]} />
      {data.length === 0 ? (
        <p className="mt-6 text-sm text-foreground-subtle">No products lost units under these filters.</p>
      ) : (
        <ResponsiveContainer width="100%" height={230}>
          <BarChart data={data} margin={{ top: 16, right: 8, bottom: 4, left: 0 }}>
            <XAxis dataKey="label" interval={0} height={36} stroke="var(--chart-ink-muted)" fontSize={11} />
            <YAxis stroke="var(--chart-ink-muted)" fontSize={11} width={40} tickFormatter={(v) => formatNumber(v)} />
            <Tooltip cursor={{ fill: "transparent" }} content={<ProductTooltip />} />
            <Bar dataKey="missing_units" radius={[4, 4, 0, 0]} maxBarSize={44} isAnimationActive={false}>
              {data.map((r) => <Cell key={`${r.sku}-${r.product}`} fill={r.pattern === "recurring" ? RECURRING : ONE_OFF} />)}
              <LabelList dataKey="missing_units" position="top" formatter={(v) => formatNumber(v)} fontSize={11} fill="var(--text)" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

function DailyTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const recurring = payload.find((p) => p.dataKey === "recurring_units")?.value ?? 0;
  const other = payload.find((p) => p.dataKey === "other_units")?.value ?? 0;
  return (
    <div className="rounded-md border border-border bg-surface px-3 py-2 text-xs shadow">
      <div className="font-semibold text-foreground-strong">{shortDate(label)}</div>
      <div className="text-foreground">Recurring products: {formatNumber(recurring)} units</div>
      <div className="text-foreground">All other products: {formatNumber(other)} units</div>
    </div>
  );
}

function DailyTrend({ rows }) {
  const data = rows.map((r) => ({ ...r, day: shortDate(r.date) }));
  return (
    <div>
      <Legend items={[
        { label: "Recurring products", color: RECURRING },
        { label: "All other products", color: ONE_OFF },
      ]} />
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} stroke="var(--chart-ink-muted)" fontSize={11} />
          <YAxis stroke="var(--chart-ink-muted)" fontSize={11} width={48} tickFormatter={(v) => formatNumber(v)} />
          <Tooltip content={<DailyTooltip />} />
          <Area type="monotone" dataKey="recurring_units" stackId="1" stroke={RECURRING} fill={RECURRING} fillOpacity={0.55} isAnimationActive={false} />
          <Area type="monotone" dataKey="other_units" stackId="1" stroke={ONE_OFF} fill={ONE_OFF} fillOpacity={0.35} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function TransferAccountabilityPanel({ data }) {
  if (!data || data.status !== "ok") return null;
  if (!data.moves) {
    return <p className="rounded-lg border border-dashed border-border p-4 text-sm text-foreground-subtle">No transfers match the filters in this result.</p>;
  }
  const scope = data.scope ?? {};
  return (
    <div className="space-y-6">
      <p className="text-sm text-foreground-subtle">
        {scope.is_filtered ? "Filtered view. " : ""}Last {data.window_days} days, all done receipts: {formatNumber(data.receipts)} receipts, {formatNumber(data.expected_units)} units expected,{" "}
        <span className="text-foreground">{formatNumber(data.missing_units)} missing ({data.missing_rate_pct}%)</span>
        {scope.is_filtered && <> of {formatNumber(scope.unfiltered_missing_units)} missing in the window</>}.
        A large bubble with a low rate is a big step that is usually fine. A high rate on a busy step is the one to check.
      </p>
      <div className="grid gap-6 lg:grid-cols-2">
        <PersonScatter title="Dispatch" role="Who marked the outbound transfer done" rows={data.dispatchers} />
        <PersonScatter title="Receiving" role="Who marked the receipt done at the outlet" rows={data.receivers} />
      </div>
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Products with the most missing units</h4>
        <div className="mt-2"><ProductBars rows={data.products} /></div>
      </div>
      {data.over_plan && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">Sent above plan</h4>
          <p className="mt-1 text-xs text-foreground-subtle">
            Outbound lines where more was sent than the transfer note planned. Company-wide, same window.
            {data.not_on_plan?.lines === 0
              ? " No product was sent without a plan line."
              : data.not_on_plan
                ? ` ${formatNumber(data.not_on_plan.lines)} receipt lines have no plan line for their product (${formatNumber(data.not_on_plan.units_received)} units).`
                : ""}
          </p>
          {data.over_plan.lines === 0 ? (
            <p className="mt-3 text-sm text-foreground-subtle">Nothing was sent above plan in this window.</p>
          ) : (
            <div className="mt-3 grid gap-4 lg:grid-cols-2">
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full min-w-[360px] text-left text-sm">
                  <thead className="border-b border-border text-xs text-foreground-subtle">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-medium">Largest lines</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Plan</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Sent</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Over</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.over_plan.top_lines.map((row) => (
                      <tr key={`${row.transfer_out}-${row.sku}`} className="border-t border-border">
                        <td className="px-3 py-2 text-foreground">
                          <span className="block">{shortProduct(row.product)}</span>
                          <span className="text-xs text-foreground-subtle">{row.transfer_out} · {displayPerson(row.dispatch_completed_by)}</span>
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.plan_qty)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.sent_qty)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.over_qty)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full min-w-[260px] text-left text-sm">
                  <thead className="border-b border-border text-xs text-foreground-subtle">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-medium">Dispatched by</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Lines</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">Units over</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.over_plan.by_dispatcher.map((row) => (
                      <tr key={row.person} className="border-t border-border">
                        <td className="px-3 py-2 text-foreground">{displayPerson(row.person)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.lines)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.units_over)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {data.over_plan.top_lines[0]?.over_qty >= 500 && (
            <p className="mt-2 text-xs text-warning">
              One line is {formatNumber(data.over_plan.top_lines[0].over_qty)} units over plan. Check it for a unit or entry error before treating it as real stock.
            </p>
          )}
        </div>
      )}

      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">When the loss happened</h4>
        <p className="mt-1 text-xs text-foreground-subtle">Missing units per receipt day. Recurring products come in bursts; the other spikes are single large lines.</p>
        <div className="mt-2"><DailyTrend rows={data.daily} /></div>
      </div>
      <details className="rounded-lg border border-border p-3">
        <summary className="cursor-pointer text-sm font-semibold text-foreground-strong">View the numbers: dispatch and receiving</summary>
        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="border-b border-border text-xs text-foreground-subtle">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Step</th>
                <th scope="col" className="px-3 py-2 font-medium">Person</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Handled</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Missing</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Rate</th>
              </tr>
            </thead>
            <tbody>
              {[...data.dispatchers.map((r) => ["Dispatch", r]), ...data.receivers.map((r) => ["Receiving", r])].map(([step, row]) => (
                <tr key={`${step}-${row.person}`} className="border-t border-border">
                  <td className="px-3 py-2 text-foreground-subtle">{step}</td>
                  <td className="px-3 py-2 text-foreground">{displayPerson(row.person)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.expected_units)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-foreground">{formatNumber(row.missing_units)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-foreground">{row.missing_rate_pct == null ? "n/a" : `${row.missing_rate_pct}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
