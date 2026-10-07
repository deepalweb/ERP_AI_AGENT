import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatNumber } from "../format";

/** Three views of the same transfer lines, one per step of the flow, on one shared
 * unit scale so the outlets compare across charts:
 *   Plan vs Sent, Sent vs Received (Received + Missing = Sent), Plan vs Received.
 * Plan is drawn as an outline (a target, not a moved quantity). Blue is what moved,
 * red is the missing quantity. Scope: only the lines that had a shortage. */

const PLAN_STYLE = { fill: "transparent", stroke: "var(--text-subtle)", strokeWidth: 1.5 };
const MOVED = "var(--series-1)";
const MISSING = "var(--status-critical)";

const shortOutlet = (name) => (name ?? "").replace("Celeste Daily", "").replace(/^[\s-]+/, "").trim() || name;

function Legend({ items }) {
  return (
    <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-foreground-subtle">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-2">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={item.outline ? { border: "1.5px solid var(--text-subtle)" } : { background: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function Panel({ title, legend, caption, children }) {
  return (
    <div className="rounded-lg border border-border p-4">
      <h3 className="text-sm font-semibold text-foreground-strong">{title}</h3>
      <div className="mt-2"><Legend items={legend} /></div>
      {children}
      {caption && <p className="mt-2 text-xs text-foreground-subtle">{caption}</p>}
    </div>
  );
}

function niceMax(values) {
  const max = Math.max(1, ...values);
  return Math.ceil(max / 100) * 100;
}

export default function TransferFlowCharts({ outlets }) {
  const rows = (outlets ?? []).map((o) => ({
    name: shortOutlet(o.outlet),
    plan: o.plan_units ?? 0,
    sent: o.sent_units ?? 0,
    received: o.line_received_units ?? 0,
    missing: o.units ?? 0,
  })).sort((a, b) => b.plan - a.plan);
  if (!rows.length) return null;

  const domain = [0, niceMax(rows.flatMap((r) => [r.plan, r.sent]))];
  const axisProps = { stroke: "var(--chart-ink-muted)", fontSize: 12, tickLine: false };
  const height = Math.max(rows.length * 56, 160);
  const gap = (a, b) => rows.map((r) => `${r.name} ${formatNumber(r[a] - r[b])}`).join(" · ");
  const plural = (n) => (n === 1 ? "unit" : "units");

  return (
    <div>
    <p className="mb-3 text-xs text-foreground-subtle">Scope: only the lines that had a shortage, not every transfer to these outlets. All three charts use the same unit scale.</p>
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <Panel
        title="Plan vs sent"
        legend={[{ label: "Planned (outline)", outline: true }, { label: "Sent (marked done)", color: MOVED }]}
        caption={`Planning gap, units planned but not sent: ${gap("plan", "sent")}.`}
      >
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }} barGap={2}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" domain={domain} hide />
            <YAxis type="category" dataKey="name" width={130} {...axisProps} />
            <Tooltip formatter={(v) => `${formatNumber(v)} ${plural(v)}`} />
            <Bar dataKey="plan" name="Planned" {...PLAN_STYLE} radius={[0, 3, 3, 0]}>
              <LabelList dataKey="plan" position="right" formatter={(v) => formatNumber(v)} fontSize={11} fill="var(--text-subtle)" />
            </Bar>
            <Bar dataKey="sent" name="Sent" fill={MOVED} radius={[0, 3, 3, 0]}>
              <LabelList dataKey="sent" position="right" formatter={(v) => formatNumber(v)} fontSize={11} fill="var(--text)" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel
        title="Sent vs received"
        legend={[{ label: "Received (marked done)", color: MOVED }, { label: "Missing (sent, not received)", color: MISSING }]}
        caption={`Each bar totals what was sent. Missing is the transit and receiving gap: ${rows.map((r) => `${r.name} ${formatNumber(r.missing)}`).join(" · ")} units.`}
      >
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
            <XAxis type="number" domain={domain} hide />
            <YAxis type="category" dataKey="name" width={130} {...axisProps} />
            <Tooltip formatter={(v, n) => `${formatNumber(v)} ${plural(v)} ${n === "received" ? "received" : "missing"}`} />
            <Bar dataKey="received" name="received" stackId="s" fill={MOVED}>
              <LabelList dataKey="received" position="insideRight" formatter={(v) => (v ? formatNumber(v) : "")} fontSize={11} fill="#ffffff" />
            </Bar>
            <Bar dataKey="missing" name="missing" stackId="s" fill={MISSING} radius={[0, 3, 3, 0]}>
              <LabelList dataKey="missing" position="right" formatter={(v) => formatNumber(v)} fontSize={11} fill="var(--text)" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>

      <Panel
        title="Plan vs received"
        legend={[{ label: "Planned (outline)", outline: true }, { label: "Received (marked done)", color: MOVED }]}
        caption={`Total gap, units planned but not received: ${gap("plan", "received")}.`}
      >
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }} barGap={2}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" domain={domain} hide />
            <YAxis type="category" dataKey="name" width={130} {...axisProps} />
            <Tooltip formatter={(v) => `${formatNumber(v)} ${plural(v)}`} />
            <Bar dataKey="plan" name="Planned" {...PLAN_STYLE} radius={[0, 3, 3, 0]}>
              <LabelList dataKey="plan" position="right" formatter={(v) => formatNumber(v)} fontSize={11} fill="var(--text-subtle)" />
            </Bar>
            <Bar dataKey="received" name="Received" fill={MOVED} radius={[0, 3, 3, 0]}>
              <LabelList dataKey="received" position="right" formatter={(v) => formatNumber(v)} fontSize={11} fill="var(--text)" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Panel>
    </div>
    </div>
  );
}
