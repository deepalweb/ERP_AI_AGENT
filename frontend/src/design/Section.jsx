/** A titled panel card — the shared wrapper Dashboard uses to group
 * related content (a KPI panel, a chart, a table) instead of letting
 * everything run together edge to edge. */
export default function Section({ title, note, children }) {
  return (
    <section className="mt-4 rounded-2xl border border-border bg-chart-surface p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-foreground-strong">{title}</h2>
        {note && <span className="text-xs text-foreground/50">{note}</span>}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}
